#!/usr/bin/env python3
"""Fail closed unless this deployment is for a published, CI-green VORAX release tag."""
from __future__ import annotations

import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
USER_AGENT = "VORAX-Release-Gate/1.0"


def github_json(path: str, token: str) -> dict:
    request = Request(
        API_ROOT + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(512).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"GitHub API request failed: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub API returned an unexpected response shape")
    return payload


def latest_verify_check(check_runs: list[dict], commit_sha: str) -> dict:
    matches = []
    for check in check_runs:
        if check.get("name") != "verify":
            continue
        suite_sha = (check.get("check_suite") or {}).get("head_sha")
        if suite_sha and suite_sha.lower() != commit_sha.lower():
            continue
        matches.append(check)
    if not matches:
        raise RuntimeError("No 'verify' CI check exists for the exact tagged commit")
    matches.sort(key=lambda item: (item.get("started_at") or "", item.get("id", 0)))
    latest = matches[-1]
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise RuntimeError(
            "Latest exact-SHA 'verify' CI check is not green: "
            f"status={latest.get('status')!r}, conclusion={latest.get('conclusion')!r}"
        )
    return latest


def validate_context(ref_type: str, ref_name: str, commit_sha: str, repository: str) -> None:
    if ref_type != "tag":
        raise RuntimeError("Deployment refused: run the workflow from a Git tag, not a branch")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", ref_name or ""):
        raise RuntimeError("GITHUB_REF_NAME is missing or invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", (commit_sha or "").lower()):
        raise RuntimeError("GITHUB_SHA must be a full 40-character commit SHA")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository or ""):
        raise RuntimeError("GITHUB_REPOSITORY must be owner/repository")


def main() -> int:
    try:
        repository = os.getenv("GITHUB_REPOSITORY", "").strip()
        ref_type = os.getenv("GITHUB_REF_TYPE", "").strip()
        ref_name = os.getenv("GITHUB_REF_NAME", "").strip()
        commit_sha = os.getenv("GITHUB_SHA", "").strip().lower()
        token = (os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or "").strip()
        validate_context(ref_type, ref_name, commit_sha, repository)
        if not token:
            raise RuntimeError("GH_TOKEN or GITHUB_TOKEN is required")

        tag = quote(ref_name, safe="")
        release = github_json(f"/repos/{repository}/releases/tags/{tag}", token)
        if release.get("draft") or not release.get("published_at"):
            raise RuntimeError("The tag does not identify a published GitHub Release")

        tagged_commit = github_json(f"/repos/{repository}/commits/{tag}", token)
        if (tagged_commit.get("sha") or "").lower() != commit_sha:
            raise RuntimeError("GitHub Release tag does not resolve to GITHUB_SHA")

        comparison = github_json(
            f"/repos/{repository}/compare/{commit_sha}...main", token
        )
        merge_base = (comparison.get("merge_base_commit") or {}).get("sha", "").lower()
        if merge_base != commit_sha:
            raise RuntimeError("Release commit is not contained in the current main history")

        checks = github_json(
            f"/repos/{repository}/commits/{commit_sha}/check-runs?per_page=100", token
        )
        verified = latest_verify_check(checks.get("check_runs") or [], commit_sha)
        print(json.dumps({
            "status": "release_gate_passed",
            "repository": repository,
            "release_tag": ref_name,
            "release_sha": commit_sha,
            "ci_check": verified.get("name"),
            "ci_check_id": verified.get("id"),
            "ci_conclusion": verified.get("conclusion"),
        }, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"VORAX release gate blocked: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
