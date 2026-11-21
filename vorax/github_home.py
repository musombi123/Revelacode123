"""GitHub Home sync and direct trusted-Runtime child-to-Home commits."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .vlang import dump_record
from .home_index import append_method, parse_method_index


class GitHubHomeError(RuntimeError):
    pass


def _api(method, path, token=None, body=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "RevelaCode-VORAX-Runtime/1.0",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        "https://api.github.com" + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", "replace")
        raise GitHubHomeError(
            f"GitHub API {method} failed ({exc.code}): {detail[:400]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise GitHubHomeError("GitHub API unavailable: " + type(exc).__name__) from exc


def fetch_method_index(repo: str, branch: str = "main", token: str = "") -> dict:
    """Read only Home's metadata index. Never fetch V-Lang method bodies."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid Home repository")
    path = (
        f"/repos/{repo}/contents/templates/library/index.json"
        f"?ref={urllib.parse.quote(branch, safe='')}"
    )
    try:
        blob = _api("GET", path, token)
    except GitHubHomeError as exc:
        raise GitHubHomeError(
            "Home metadata index is unavailable; refusing to learn from Home method contents"
        ) from exc
    if blob.get("encoding") != "base64":
        raise GitHubHomeError("Home method index is not base64 content")
    try:
        raw = base64.b64decode(blob["content"], validate=False)
        return parse_method_index(raw)
    except Exception as exc:
        raise GitHubHomeError("Home method index failed validation") from exc


def _read_index_at_commit(repo: str, commit_sha: str, token: str) -> dict:
    path = (
        f"/repos/{repo}/contents/templates/library/index.json"
        f"?ref={urllib.parse.quote(commit_sha, safe='')}"
    )
    blob = _api("GET", path, token)
    if blob.get("encoding") != "base64":
        raise GitHubHomeError("Home method index is not base64 content")
    try:
        return parse_method_index(base64.b64decode(blob["content"], validate=False))
    except Exception as exc:
        raise GitHubHomeError("Home method index failed validation") from exc


def _assert_path_absent(repo: str, path: str, commit_sha: str, token: str) -> None:
    quoted_path = urllib.parse.quote(path, safe="/")
    query = urllib.parse.quote(commit_sha, safe="")
    try:
        _api("GET", f"/repos/{repo}/contents/{quoted_path}?ref={query}", token)
    except GitHubHomeError as exc:
        if "(404)" in str(exc):
            return
        raise
    raise GitHubHomeError(
        f"Home already contains {path} outside its metadata index; refusing to overwrite it"
    )



def create_home_pull_request(
    repo: str,
    base_branch: str,
    child_id: str,
    template: dict,
    token: str,
) -> dict:
    """Publish a verified proposal on an isolated branch and open a Home PR.

    This function never updates the Home base branch. The candidate and metadata
    index are committed together on a proposal branch; promotion remains blocked
    until Runtime later observes the exact metadata digest on the merged base branch.
    """
    if not token:
        raise GitHubHomeError("VORAX_HOME_WRITE_TOKEN is not configured on this Runtime")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid Home repository")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", base_branch) or ".." in base_branch.split("/"):
        raise ValueError("Invalid Home base branch")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,39}", child_id):
        raise ValueError("Invalid child ID")

    record = dict(template)
    provenance = dict(record.get("provenance", {}))
    provenance.update({"kind": "teacher", "child_id": child_id})
    record["provenance"] = provenance
    content = dump_record(record)
    method_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    encoded_base = urllib.parse.quote(base_branch, safe="")
    ref = _api("GET", f"/repos/{repo}/git/ref/heads/{encoded_base}", token)
    base_sha = ref.get("object", {}).get("sha")
    if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise GitHubHomeError("GitHub returned an invalid Home base commit")
    base_commit = _api("GET", f"/repos/{repo}/git/commits/{base_sha}", token)
    base_tree = base_commit.get("tree", {}).get("sha")
    if not isinstance(base_tree, str) or not base_tree:
        raise GitHubHomeError("GitHub returned an invalid Home Git tree")

    home_index = _read_index_at_commit(repo, base_sha, token)
    try:
        updated_index = append_method(home_index, record)
    except ValueError as exc:
        raise GitHubHomeError("Home metadata index reports a duplicate method: " + str(exc)) from exc

    slug = re.sub(r"[^a-z0-9_-]+", "_", record["id"].lower()).strip("_")
    method_path = f"templates/library/children/{child_id}_{slug}.vlang"
    _assert_path_absent(repo, method_path, base_sha, token)

    proposal_branch = (
        "vorax/proposals/"
        + child_id
        + "-"
        + slug[:72]
        + "-"
        + method_sha[:12]
        + "-"
        + str(time.time_ns())
    )
    _api(
        "POST",
        f"/repos/{repo}/git/refs",
        token,
        {"ref": "refs/heads/" + proposal_branch, "sha": base_sha},
    )

    index_path = "templates/library/index.json"
    index_content = json.dumps(updated_index, indent=2, ensure_ascii=False) + "\n"
    tree = _api("POST", f"/repos/{repo}/git/trees", token, {
        "base_tree": base_tree,
        "tree": [
            {
                "path": method_path,
                "mode": "100644",
                "type": "blob",
                "content": content,
            },
            {
                "path": index_path,
                "mode": "100644",
                "type": "blob",
                "content": index_content,
            },
        ],
    })
    tree_sha = tree.get("sha")
    if not isinstance(tree_sha, str) or not tree_sha:
        raise GitHubHomeError("GitHub did not create the proposal Git tree")

    commit = _api("POST", f"/repos/{repo}/git/commits", token, {
        "message": f"vorax: propose verified knowledge from child {child_id}: {record['id']}",
        "tree": tree_sha,
        "parents": [base_sha],
    })
    commit_sha = commit.get("sha")
    if not isinstance(commit_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise GitHubHomeError("GitHub did not create the proposal commit")

    updated_ref = _api(
        "PATCH",
        f"/repos/{repo}/git/refs/heads/{urllib.parse.quote(proposal_branch, safe='')}",
        token,
        {"sha": commit_sha, "force": False},
    )
    if updated_ref.get("object", {}).get("sha") != commit_sha:
        raise GitHubHomeError("GitHub did not confirm the proposal branch update")

    pr = _api("POST", f"/repos/{repo}/pulls", token, {
        "title": f"VORAX knowledge proposal: {record['id']}",
        "head": proposal_branch,
        "base": base_branch,
        "body": (
            "## VORAX verified knowledge proposal\n\n"
            f"- Child: {child_id}\n"
            f"- Method: {record['id']}\n"
            f"- Canonical method SHA-256: {method_sha}\n"
            "- Candidate was Teacher-attested and independently executed by the trusted Runtime tests.\n"
            "- Vault status remains pending until this PR is merged and Home's metadata index confirms the exact digest.\n"
            "- This proposal branch is isolated; the learning Runtime does not write to the Home base branch.\n\n"
            "Merge only after the required trusted CI checks and human review succeed."
        ),
    })
    pr_number = pr.get("number")
    pr_url = pr.get("html_url")
    if not isinstance(pr_number, int) or not isinstance(pr_url, str) or not pr_url:
        raise GitHubHomeError("GitHub did not return a valid Home pull request receipt")

    return {
        "pr_number": pr_number,
        "pr_url": pr_url,
        "branch": proposal_branch,
        "base_branch": base_branch,
        "proposal_commit_sha": commit_sha,
        "template_id": record["id"],
        "method_sha256": method_sha,
        "file": method_path,
        "index_file": index_path,
        "home_authority": "pull_request_review_and_merge_required",
    }
