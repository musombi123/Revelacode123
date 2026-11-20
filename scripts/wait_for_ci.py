#!/usr/bin/env python3
"""Wait for successful trusted VORAX core CI on the exact pull-request head SHA."""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

repo = os.environ["GITHUB_REPOSITORY"]
head_sha = os.environ["PR_HEAD_SHA"]
token = os.environ["GH_TOKEN"]
timeout = min(max(int(os.getenv("VORAX_CI_WAIT_SECONDS", "1200")), 60), 3600)
api = "https://api.github.com"
headers = {
    "Accept": "application/vnd.github+json",
    "Authorization": "Bearer " + token,
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "VORAX-Home-CI-Gate",
}


def get(path):
    request = Request(api + path, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise SystemExit(f"Unable to inspect trusted CI status: HTTP {exc.code}") from exc


deadline = time.monotonic() + timeout
query = urlencode({"head_sha": head_sha, "event": "pull_request", "per_page": 50})
path = f"/repos/{repo}/actions/workflows/ci.yml/runs?{query}"
while time.monotonic() < deadline:
    runs = get(path).get("workflow_runs", [])
    exact = [run for run in runs if run.get("head_sha") == head_sha]
    if exact:
        latest = max(exact, key=lambda run: run.get("run_number", 0))
        status = latest.get("status")
        conclusion = latest.get("conclusion")
        print(f"VORAX core CI: status={status} conclusion={conclusion} url={latest.get('html_url')}")
        if status == "completed":
            if conclusion == "success":
                print("PASS: trusted VORAX core CI succeeded on the exact PR head SHA")
                sys.exit(0)
            raise SystemExit(f"NO-GO: VORAX core CI conclusion is {conclusion}")
    else:
        print("Waiting for VORAX core CI run to appear on the PR head SHA...")
    time.sleep(15)

raise SystemExit("NO-GO: VORAX core CI did not complete successfully within the configured window")
