#!/usr/bin/env python3
"""Trusted Home validation for data-only improvement pull requests."""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import PurePosixPath

repo = os.environ["GITHUB_REPOSITORY"]
number = os.environ["PR_NUMBER"]
token = os.environ["GITHUB_TOKEN"]
api = "https://api.github.com"
headers = {
    "Accept": "application/vnd.github+json",
    "Authorization": "Bearer " + token,
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "VORAX-Home-Data-Verifier",
}
MAX_JSON = 200_000
MAX_DOC = 120_000
FORBIDDEN_KEYS = {
    "api_key", "apikey", "password", "passwd", "secret", "token",
    "authorization", "private_key", "privatekey", "shell", "command",
    "script", "executable", "workflow", "action",
}
SECRET_PATTERN = re.compile(
    r"(?i)(api[_ -]?key|password|passwd|secret|token|authorization)\s*[:=]\s*\S+"
)


def get(path):
    request = urllib.request.Request(api + path, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fail(message):
    raise SystemExit("REJECT: " + message)


def allowed(path):
    if path.startswith("docs/vorax/") and path.endswith((".md", ".txt")):
        return "doc"
    if path in {"templates/library/index.json", "templates/library/vlang/index.json",
                "templates/library/vlang/pending/index.json"}:
        return "json"
    if path.startswith("manifest/") and path.endswith(".json"):
        return "json"
    if path.startswith("reports/") and path.endswith(".json"):
        return "json"
    return None


def inspect_json(value, path="$"):
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9_]", "", str(key).casefold())
            if normalized in FORBIDDEN_KEYS:
                fail(f"JSON field {path}.{key} is not allowed in data-only auto-merge")
            inspect_json(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            inspect_json(item, f"{path}[{index}]")
    elif isinstance(value, str):
        if SECRET_PATTERN.search(value):
            fail(f"credential-like string found at {path}")


pr = get(f"/repos/{repo}/pulls/{number}")
if pr.get("base", {}).get("ref") != "main":
    fail("data-only proposals must target main")
head_repo = pr.get("head", {}).get("repo") or {}
if head_repo.get("full_name") != repo:
    fail("only same-repository data-only proposals are eligible")
files = get(f"/repos/{repo}/pulls/{number}/files?per_page=100")
changed_paths = {item.get("filename", "") for item in files}

# This workflow is triggered when a PR touches at least one data-eligible path.
# Mixed/code PRs can also touch those paths, but must never enter the data-only
# auto-merge lane. Leave them to ordinary engineering CI and human review.
non_data_paths = {path for path in changed_paths if allowed(path) is None}
if non_data_paths:
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write("eligible=false\\n")
        output.write("checked_files=0\\n")
    print(
        "INFO: mixed/code PR detected; data-only auto-merge skipped: "
        + ", ".join(sorted(non_data_paths))
    )
    raise SystemExit(0)
if "templates/library/index.json" in changed_paths:
    # Child method + index proposals are validated by the stricter V-Lang gate, not this JSON-only gate.
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write("eligible=false\n")
        output.write("checked_files=0\n")
    print("INFO: Home index update deferred to the trusted V-Lang proposal gate; data-only auto-merge skipped")
    raise SystemExit(0)
if not files or len(files) > 30:
    fail("data-only proposal must contain 1–30 files")
checked = []
for item in files:
    path = item.get("filename", "")
    kind = allowed(path)
    if not kind:
        fail(f"path is outside the data-only allowlist: {path}")
    if item.get("status") not in {"added", "modified"}:
        fail(f"deletions and renames are not eligible: {path}")
    encoded_path = urllib.parse.quote(path, safe="/")
    blob = get(f"/repos/{repo}/contents/{encoded_path}?ref={pr['head']['sha']}")
    if blob.get("encoding") != "base64":
        fail(f"unsupported content encoding: {path}")
    try:
        raw = base64.b64decode(blob["content"], validate=False)
        content = raw.decode("utf-8")
    except Exception:
        fail(f"content is not valid UTF-8: {path}")
    limit = MAX_DOC if kind == "doc" else MAX_JSON
    if not raw or len(raw) > limit:
        fail(f"file is empty or exceeds {limit} bytes: {path}")
    if kind == "doc":
        if re.search(r"(?is)<\s*(script|iframe|object|embed)\b", content) or re.search(r"(?i)javascript\s*:", content):
            fail(f"active HTML or javascript URL is not permitted in docs: {path}")
        if SECRET_PATTERN.search(content):
            fail(f"credential-like content found in docs: {path}")
    else:
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            fail(f"invalid JSON in {path}: {exc}")
        if not isinstance(value, dict):
            fail(f"JSON root must be an object: {path}")
        inspect_json(value)
    checked.append(path)

print("PASS: data-only files are within the allowlist and contain no executable payloads or credential-like fields")
with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
    output.write("eligible=true\n")
    output.write(f"checked_files={len(checked)}\n")
