"""GitHub-scheduled coordinator for the VORAX Runtime + Kaggle Hunter Mesh.

This process coordinates issues and kernels only. It performs no model inference,
does not read Home method bodies, and never writes directly to Home or the HF Vault.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

# Running "python scripts/vorax_weekly_coordinator.py" places scripts/ on
# sys.path[0]; explicitly add the checkout root before importing the package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from vorax.network import NetworkError, request_json
from vorax.scheduler import start_runtime_cycle
from vorax.weekly_cycle import HUNTER_SLICES, current_cycle_id

HOME_REPO = "musombi123/Revelacode123"
DEFAULT_RUNTIME = "https://musombi-vorax-runtime-service.hf.space"
HUNTER_TIMEOUT_SECONDS = 7200
KAGGLE_PUSH_TIMEOUT_SECONDS = 180


def _headers(token: str) -> dict:
    return {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def ensure_weekly_issue(cycle_id: str, github_token: str) -> tuple[str | None, str]:
    title = f"VORAX weekly knowledge-growth cycle {cycle_id}"
    query = urllib.parse.quote_plus(f'repo:{HOME_REPO} is:issue is:open in:title "{title}"')
    try:
        search = request_json(
            "https://api.github.com/search/issues?q=" + query,
            headers=_headers(github_token),
            timeout=30,
            attempts=3,
        )
        for item in search.get("items", []):
            if item.get("title") == title and item.get("html_url"):
                return item["html_url"], "existing"
        created = request_json(
            f"https://api.github.com/repos/{HOME_REPO}/issues",
            method="POST",
            payload={
                "title": title,
                "body": (
                    "## VORAX weekly knowledge-growth cycle\n\n"
                    f"- Cycle: {cycle_id}\n"
                    "- Home is the source of truth for metadata and approved methods.\n"
                    "- Kaggle Children are isolated discovery hunters and communicate only with Runtime.\n"
                    "- Runtime owns Vault pending writes and isolated Home pull requests.\n"
                    "- Learning uses procedures, not model weights. Paid inference remains disabled.\n"
                    "- Weekly report: reports/weekly/" + cycle_id + ".json (Vault + Home PR).\n"
                    "- This issue tracks orchestration; it does not approve any method."
                ),
            },
            headers=_headers(github_token),
            timeout=30,
            attempts=3,
        )
        url = created.get("html_url")
        if not isinstance(url, str) or not url:
            raise NetworkError("GitHub did not return a weekly issue URL")
        return url, "created"
    except Exception as exc:
        # Home issue availability must not block Runtime/Vault learning.
        print("weekly_issue_status=pending reason=" + type(exc).__name__)
        return None, "pending"


def _runtime_request(runtime_url: str, child_key: str, path: str, payload: dict | None = None) -> dict:
    headers = {"x-child-id": "kaggle-child", "x-child-key": child_key}
    if payload is None:
        result = request_json(
            runtime_url.rstrip("/") + path,
            headers=headers,
            timeout=30,
            attempts=3,
        )
    else:
        result = request_json(
            runtime_url.rstrip("/") + path,
            method="POST",
            payload=payload,
            headers=headers,
            timeout=30,
            attempts=3,
        )
    return result


def _kernel_ref(username: str, discovery_slice: str) -> str:
    return f"{username}/vorax-hunter-{discovery_slice}"


def recover_cached_reports(
    runtime_url: str,
    child_key: str,
    username: str,
    cycle_id: str,
    work_dir: Path,
) -> int:
    """Recover report files from prior Kaggle outputs when Runtime delivery failed."""
    recovered = 0
    for hunter_id, discovery_slice in HUNTER_SLICES:
        kernel_ref = _kernel_ref(username, discovery_slice)
        output_dir = work_dir / ("recover-" + hunter_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [
            "kaggle", "kernels", "output", kernel_ref,
            "-p", str(output_dir),
            "--file-pattern", r".*vorax-learning-report\.json$",
            "-q",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        for report_path in output_dir.rglob("vorax-learning-report.json"):
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                report.get("cycle_id") != cycle_id
                or report.get("hunter_id") != hunter_id
                or report.get("runtime_delivery", {}).get("status") != "pending_retry"
            ):
                continue
            attempt = report.get("attempt", 0)
            try:
                receipt = _runtime_request(
                    runtime_url,
                    child_key,
                    "/vorax/cycle/hunter-report",
                    {
                        "cycle_id": cycle_id,
                        "hunter_id": hunter_id,
                        "attempt": attempt,
                        "report": report,
                    },
                )
                print(
                    f"recovered hunter={hunter_id} cycle={cycle_id} "
                    f"runtime_status={receipt.get('status')}"
                )
                recovered += 1
            except Exception as exc:
                print("cached_report_delivery_pending hunter=" + hunter_id + " reason=" + type(exc).__name__)
            break
    return recovered


def _make_kernel_folder(
    root: Path,
    connector_path: Path,
    username: str,
    cycle_id: str,
    assignment: dict,
    curriculum_task: dict | None,
) -> Path:
    hunter_id = assignment["hunter_id"]
    discovery_slice = assignment["discovery_slice"]
    folder = root / hunter_id
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(connector_path, folder / "vorax_child.py")
    config = {
        "cycle_id": cycle_id,
        "hunter_id": hunter_id,
        "discovery_slice": discovery_slice,
        "attempt": int(assignment.get("attempt", 0)),
        "curriculum_task": curriculum_task if hunter_id == "hunter-1" else None,
        "max_runtime_seconds": HUNTER_TIMEOUT_SECONDS,
    }
    (folder / "hunter-config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    wrapper = '''from __future__ import annotations
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
config = json.loads((root / "hunter-config.json").read_text(encoding="utf-8"))
from vorax_child import main

args = [
    "--cycle-id", config["cycle_id"],
    "--hunter-id", config["hunter_id"],
    "--hunter-slice", config["discovery_slice"],
    "--attempt", str(config["attempt"]),
    "--max-runtime-seconds", str(config["max_runtime_seconds"]),
    "--discovery-pages", "3",
]
if config.get("curriculum_task"):
    args.extend(["--cycle-task", json.dumps(config["curriculum_task"], ensure_ascii=False)])
sys.argv = ["vorax_child.py", *args]
raise SystemExit(main())
'''
    (folder / "hunter.py").write_text(wrapper, encoding="utf-8")
    metadata = {
        "id": f"{username}/vorax-hunter-{discovery_slice}",
        "code_file": "hunter.py",
        "language": "python",
        "kernel_type": "script",
        "is_private": True,
        "enable_gpu": False,
        "enable_internet": True,
        "dataset_sources": [],
        "competition_sources": [],
        "kernel_sources": [],
        "model_sources": [],
    }
    (folder / "kernel-metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return folder


def dispatch_hunter(
    runtime_url: str,
    child_key: str,
    username: str,
    assignment: dict,
    cycle_id: str,
    curriculum_task: dict | None,
    connector_path: Path,
    temporary_root: Path,
) -> bool:
    hunter_id = assignment["hunter_id"]
    discovery_slice = assignment["discovery_slice"]
    attempt = int(assignment.get("attempt", 0))
    try:
        _runtime_request(
            runtime_url,
            child_key,
            "/vorax/cycle/status",
            {
                "cycle_id": cycle_id,
                "hunter_id": hunter_id,
                "attempt": attempt,
                "status": "running",
            },
        )
        folder = _make_kernel_folder(
            temporary_root,
            connector_path,
            username,
            cycle_id,
            assignment,
            curriculum_task,
        )
        environment = os.environ.copy()
        environment["KAGGLE_USERNAME"] = username
        command = [
            "kaggle", "kernels", "push",
            "-p", str(folder),
            "--timeout", str(HUNTER_TIMEOUT_SECONDS),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=KAGGLE_PUSH_TIMEOUT_SECONDS,
            check=False,
            env=environment,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Kaggle CLI push failed")[-500:]
            raise RuntimeError(detail)
        print(f"launched hunter={hunter_id} slice={discovery_slice} attempt={attempt}")
        return True
    except Exception as exc:
        try:
            _runtime_request(
                runtime_url,
                child_key,
                "/vorax/cycle/status",
                {
                    "cycle_id": cycle_id,
                    "hunter_id": hunter_id,
                    "attempt": attempt,
                    "status": "failed",
                    "reason": type(exc).__name__,
                },
            )
        except Exception as status_exc:
            print("hunter_failure_status_delivery_pending=" + type(status_exc).__name__)
        print("hunter_dispatch_failed=" + hunter_id + " reason=" + type(exc).__name__)
        return False


def main() -> int:
    runtime_url = os.getenv("VORAX_RUNTIME_URL", DEFAULT_RUNTIME).rstrip("/")
    child_key = os.getenv("VORAX_KAGGLE_CHILD_API_KEY", "").strip()
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    username = os.getenv("KAGGLE_USERNAME", "").strip()
    kaggle_token = os.getenv("KAGGLE_API_TOKEN", "").strip()
    if not re.fullmatch(r"https://[^\s]+", runtime_url):
        raise SystemExit("VORAX_RUNTIME_URL must be an HTTPS URL")
    missing = [
        name for name, value in (
            ("VORAX_KAGGLE_CHILD_API_KEY", child_key),
            ("KAGGLE_USERNAME", username),
            ("KAGGLE_API_TOKEN", kaggle_token),
        ) if not value
    ]
    if missing:
        raise SystemExit("Missing required GitHub environment values: " + ", ".join(missing))

    os.environ["KAGGLE_API_TOKEN"] = kaggle_token
    os.environ["KAGGLE_USERNAME"] = username
    cycle_id = current_cycle_id()
    github_issue_url, issue_state = ensure_weekly_issue(cycle_id, github_token) if github_token else (None, "pending")
    connector_path = Path("kaggle/child/vorax_child.py")
    if not connector_path.is_file():
        raise SystemExit("Kaggle learning connector source was not found in the checkout")

    with tempfile.TemporaryDirectory(prefix="vorax-weekly-") as temp:
        temp_root = Path(temp)
        recover_cached_reports(runtime_url, child_key, username, cycle_id, temp_root)

        # Do not dispatch new work unless Runtime explicitly confirms its Vault,
        # Home metadata and required gateway credentials are ready.
        runtime_health = _runtime_request(runtime_url, child_key, "/health")
        if runtime_health.get("service") != "runtime" or runtime_health.get("ready") is not True:
            raise SystemExit("Runtime is not ready; no new Kaggle hunters were launched.")

        # Start is idempotent. Runtime stores the manifest and owns every Vault write.
        try:
            response = start_runtime_cycle(
                runtime_url,
                child_id="kaggle-child",
                child_key=child_key,
                cycle_id=cycle_id,
                home_issue_url=github_issue_url,
            )
        except Exception as exc:
            raise SystemExit("Runtime cycle start failed; no Kaggle kernels were launched: " + type(exc).__name__)

        manifest = response.get("manifest") or {}
        cycle_status = response.get("status") or manifest.get("status")
        print(
            f"cycle={cycle_id} status={cycle_status} issue={issue_state} "
            f"curriculum_task={(manifest.get('curriculum_task') or {}).get('id')}"
        )
        if cycle_status in {"paused_teacher", "retry_scheduled", "complete", "failed"}:
            print("No hunters dispatched; Runtime reports a paused or terminal cycle.")
            return 0

        curriculum_task = manifest.get("curriculum_task")
        queued = [
            item for item in manifest.get("hunter_assignments", [])
            if item.get("status") == "queued"
        ]
        if not queued:
            print("No queued hunters; active workers are left undisturbed.")
            return 0

        results = []
        for assignment in queued:
            results.append(dispatch_hunter(
                runtime_url,
                child_key,
                username,
                assignment,
                cycle_id,
                curriculum_task,
                connector_path,
                temp_root,
            ))
        print(
            f"cycle={cycle_id} dispatched={sum(results)} "
            f"dispatch_failed={len(results)-sum(results)}"
        )
        return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
