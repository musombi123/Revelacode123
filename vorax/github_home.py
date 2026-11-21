"""GitHub Home metadata reads and isolated, reviewed Child knowledge proposals."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .vlang import dump_record
from .home_index import append_method, parse_method_index
from .network import NetworkError, request_json


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
    try:
        return request_json(
            "https://api.github.com" + path,
            method=method,
            payload=body,
            headers=headers,
            timeout=30,
            attempts=3,
        )
    except NetworkError as exc:
        message = str(exc)
        match = re.search(r"HTTP (\d{3})", message)
        status = match.group(1) if match else "network"
        raise GitHubHomeError(f"GitHub API {method} failed ({status})") from exc


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
        f"/repos/{repo}/git/refs/heads/{urllib.parse.quote(proposal_branch, safe='/')}",
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


def is_merged_home_pull_request(
    repo: str,
    pull_request_url: str,
    base_branch: str,
    token: str,
) -> bool:
    """Confirm a pending proposal's exact Home PR was merged into the expected base.

    A matching main-branch metadata digest is necessary but not sufficient by itself:
    Runtime also requires GitHub's canonical PR record to say merged=true, closed,
    with the expected base and a VORAX proposal head from this same repository.
    """
    if not token:
        raise GitHubHomeError("Home read credentials are required to verify a merged proposal PR")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid Home repository")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", base_branch) or ".." in base_branch.split("/"):
        raise ValueError("Invalid Home base branch")

    parsed = urllib.parse.urlparse(pull_request_url)
    match = re.fullmatch(r"/" + re.escape(repo) + r"/pull/([1-9][0-9]*)", parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
        or not match
    ):
        raise GitHubHomeError("Vault contains an invalid Home pull request URL")

    pull_number = int(match.group(1))
    pr = _api("GET", f"/repos/{repo}/pulls/{pull_number}", token)
    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    base_repo = base.get("repo") if isinstance(base.get("repo"), dict) else {}
    head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}

    return bool(
        pr.get("number") == pull_number
        and pr.get("state") == "closed"
        and pr.get("merged") is True
        and base.get("ref") == base_branch
        and base_repo.get("full_name", "").casefold() == repo.casefold()
        and isinstance(head.get("ref"), str)
        and head["ref"].startswith("vorax/proposals/")
        and head_repo.get("full_name", "").casefold() == repo.casefold()
        and isinstance(pr.get("merge_commit_sha"), str)
        and re.fullmatch(r"[0-9a-f]{40}", pr["merge_commit_sha"]) is not None
    )



def create_home_report_pull_request(
    repo: str,
    base_branch: str,
    report: dict,
    token: str,
) -> dict:
    """Submit a weekly JSON report through an isolated Home PR; never write the base branch."""
    from datetime import datetime, timezone

    if not token:
        raise GitHubHomeError("VORAX_HOME_WRITE_TOKEN is not configured on this Runtime")
    cycle_id = report.get("cycle_id") if isinstance(report, dict) else None
    if not isinstance(cycle_id, str) or not re.fullmatch(r"\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])", cycle_id):
        raise GitHubHomeError("Weekly report has an invalid ISO cycle ID")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid Home repository")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", base_branch) or ".." in base_branch.split("/"):
        raise ValueError("Invalid Home base branch")

    raw_text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    report_sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    report_path = f"reports/weekly/{cycle_id}.json"
    encoded_base = urllib.parse.quote(base_branch, safe="")
    ref = _api("GET", f"/repos/{repo}/git/ref/heads/{encoded_base}", token)
    base_sha = ref.get("object", {}).get("sha")
    if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise GitHubHomeError("GitHub returned an invalid Home base commit")
    base_commit = _api("GET", f"/repos/{repo}/git/commits/{base_sha}", token)
    base_tree = base_commit.get("tree", {}).get("sha")
    if not isinstance(base_tree, str) or not base_tree:
        raise GitHubHomeError("GitHub returned an invalid Home Git tree")

    quoted_path = urllib.parse.quote(report_path, safe="/")
    try:
        existing = _api(
            "GET",
            f"/repos/{repo}/contents/{quoted_path}?ref={urllib.parse.quote(base_sha, safe='')}",
            token,
        )
    except GitHubHomeError as exc:
        if "(404)" not in str(exc):
            raise
        existing = None
    if isinstance(existing, dict):
        if existing.get("encoding") == "base64":
            try:
                existing_text = base64.b64decode(existing.get("content", "")).decode("utf-8")
            except Exception as exc:
                raise GitHubHomeError("Existing Home weekly report is not valid UTF-8") from exc
            if hashlib.sha256(existing_text.encode("utf-8")).hexdigest() == report_sha:
                return {
                    "status": "already_in_home",
                    "cycle_id": cycle_id,
                    "report_sha256": report_sha,
                    "file": report_path,
                    "home_authority": "existing_base_branch_report",
                }
        raise GitHubHomeError("Home already contains a different weekly report; refusing to overwrite it")

    proposal_branch = f"vorax/weekly-reports/{cycle_id.lower()}-{report_sha[:12]}"
    existing_prs = _api(
        "GET",
        f"/repos/{repo}/pulls?state=open&head={urllib.parse.quote(repo.split('/')[0] + ':' + proposal_branch, safe=':')}&base={urllib.parse.quote(base_branch, safe='')}",
        token,
    )
    if isinstance(existing_prs, list) and existing_prs:
        existing_pr = existing_prs[0]
        body_text = existing_pr.get("body") or ""
        if (
            f"Report SHA-256: {report_sha}" in body_text
            and isinstance(existing_pr.get("html_url"), str)
        ):
            return {
                "status": "submitted_for_home_review",
                "cycle_id": cycle_id,
                "pr_number": existing_pr.get("number"),
                "pr_url": existing_pr["html_url"],
                "branch": proposal_branch,
                "report_sha256": report_sha,
                "file": report_path,
                "home_authority": "pull_request_review_and_merge_required",
            }
        raise GitHubHomeError("A conflicting weekly report pull request already uses the proposal branch")
    _api(
        "POST",
        f"/repos/{repo}/git/refs",
        token,
        {"ref": "refs/heads/" + proposal_branch, "sha": base_sha},
    )
    tree = _api("POST", f"/repos/{repo}/git/trees", token, {
        "base_tree": base_tree,
        "tree": [{
            "path": report_path,
            "mode": "100644",
            "type": "blob",
            "content": raw_text,
        }],
    })
    tree_sha = tree.get("sha")
    if not isinstance(tree_sha, str) or not tree_sha:
        raise GitHubHomeError("GitHub did not create the weekly report tree")
    commit = _api("POST", f"/repos/{repo}/git/commits", token, {
        "message": f"vorax: weekly knowledge-growth report {cycle_id}",
        "tree": tree_sha,
        "parents": [base_sha],
    })
    commit_sha = commit.get("sha")
    if not isinstance(commit_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise GitHubHomeError("GitHub did not create the weekly report commit")
    updated_ref = _api(
        "PATCH",
        f"/repos/{repo}/git/refs/heads/{urllib.parse.quote(proposal_branch, safe='/')}",
        token,
        {"sha": commit_sha, "force": False},
    )
    if updated_ref.get("object", {}).get("sha") != commit_sha:
        raise GitHubHomeError("GitHub did not confirm the weekly report proposal branch")
    pr = _api("POST", f"/repos/{repo}/pulls", token, {
        "title": f"VORAX weekly knowledge report: {cycle_id}",
        "head": proposal_branch,
        "base": base_branch,
        "body": (
            "## VORAX weekly knowledge-growth report\n\n"
            f"- Cycle: {cycle_id}\n"
            f"- Report SHA-256: {report_sha}\n"
            "- Report was written by the trusted Runtime after hunter report validation.\n"
            "- This is an isolated report-only branch; Home base is not modified directly.\n"
            "- Merge only after the required trusted CI checks and human review succeed."
        ),
    })
    pr_number = pr.get("number")
    pr_url = pr.get("html_url")
    if not isinstance(pr_number, int) or not isinstance(pr_url, str) or not pr_url:
        raise GitHubHomeError("GitHub did not return a weekly report PR receipt")
    return {
        "status": "submitted_for_home_review",
        "cycle_id": cycle_id,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "branch": proposal_branch,
        "proposal_commit_sha": commit_sha,
        "report_sha256": report_sha,
        "file": report_path,
        "home_authority": "pull_request_review_and_merge_required",
    }
