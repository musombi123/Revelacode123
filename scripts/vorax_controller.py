#!/usr/bin/env python3
"""GitHub Actions scheduler: probe Children and start bounded Kaggle work; never run VORAX."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://api.github.com"
REPO = os.getenv("GITHUB_REPOSITORY", "musombi123/Revelacode123")
REPORT = Path(os.getenv("VORAX_CONTROLLER_REPORT", "controller-report.jsonl"))
CAMPAIGN_TITLE = "[VORAX] 7-day learning campaign"
QUEUE_TITLE = "[VORAX-X] "
UTC = timezone.utc


def now():
    return datetime.now(UTC)


def stamp(value=None):
    return (value or now()).isoformat(timespec="seconds").replace("+00:00", "Z")


def gh(method, path, payload=None):
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GitHub Actions token is not configured")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "VORAX-Self-Ping-Controller",
    }
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(API + path, data=body, headers=headers, method=method)
    with urlopen(request, timeout=25) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


def log(child="controller", **fields):
    row = {
        "child": child,
        "x": fields.pop("x", None),
        "method": fields.pop("method", None),
        "verification": fields.pop("verification", None),
        "pr": fields.pop("pr", None),
        "artifact_sha256": fields.pop("artifact_sha256", None),
        "artifact_size_bytes": fields.pop("artifact_size_bytes", None),
        "timestamp_utc": stamp(),
        "failure": fields.pop("failure", None),
        "uptime_status": fields.pop("uptime_status", "not_observed"),
        **fields,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    return row


def probe(child, base):
    if not base:
        return log(child, uptime_status="not_configured", failure="Child URL is not configured")
    if not base.startswith("https://"):
        return log(child, uptime_status="rejected_url", failure="HTTPS is required")
    headers = {"User-Agent": "VORAX-Self-Ping"}
    if child == "hf-backup-child" and os.getenv("HF_SPACE_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["HF_SPACE_TOKEN"]
    try:
        request = Request(base.rstrip("/") + "/health", headers=headers)
        with urlopen(request, timeout=18) as response:
            data = json.loads(response.read(65536).decode("utf-8"))
            healthy = response.status == 200 and data.get("status") == "ok" and data.get("engine") == "VORAX"
            return log(
                child,
                uptime_status="healthy" if healthy else "unhealthy_response",
                verification="health_confirmed" if healthy else None,
                failure=None if healthy else "Health response did not confirm VORAX",
                health=data,
            )
    except HTTPError as exc:
        detail = exc.read(256).decode(errors="replace")
        asleep = child == "koyeb-child" and exc.code in (502, 503, 504)
        return log(
            child,
            uptime_status="sleep_or_platform_unavailable" if asleep else "http_error",
            http_status=exc.code,
            failure=("Koyeb asleep, retry later (not independently confirmed)" if asleep else f"HTTP {exc.code}: {detail}"),
        )
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        state = "sleep_or_unreachable" if child == "koyeb-child" else "unreachable"
        return log(child, uptime_status=state, failure=f"{type(exc).__name__}: {str(exc)[:180]}")


def issues():
    found = []
    for page in range(1, 6):
        batch = gh("GET", f"/repos/{REPO}/issues?state=open&per_page=100&page={page}")
        found.extend(item for item in batch if "pull_request" not in item)
        if len(batch) < 100:
            break
    return found


def issue_payload(issue, marker):
    body = issue.get("body") or ""
    if marker not in body:
        return None
    raw = body.split(marker, 1)[1]
    raw = raw.split("\n\nDo not put", 1)[0].strip()
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def campaign():
    matches = [item for item in issues() if item.get("title") == CAMPAIGN_TITLE]
    return max(matches, key=lambda item: item.get("created_at", "")) if matches else None


def queue_items():
    pending = []
    for item in issues():
        if not str(item.get("title", "")).startswith(QUEUE_TITLE):
            continue
        payload = issue_payload(item, "<!-- VORAX_X_QUEUE_V1 -->")
        if payload and payload.get("version") == "vorax-x-queue/v1" and payload.get("status") == "queued":
            pending.append((item, payload))
    return pending


def queue_x(value):
    value = (value or "").strip()
    if not value or len(value) > 12000:
        raise ValueError("X must contain 1–12000 characters")
    if re.search(r"(?i)(api[_ -]?key|password|secret|token)\s*[:=]\s*\S+", value):
        raise ValueError("Credential-like X was rejected")
    payload = {
        "version": "vorax-x-queue/v1",
        "status": "queued",
        "x": value,
        "queued_at": stamp(),
        "source": "workflow_dispatch",
    }
    body = (
        "<!-- VORAX_X_QUEUE_V1 -->\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + "\n\nDo not put passwords, API keys, private records, or sensitive personal data in this queue."
    )
    issue = gh("POST", f"/repos/{REPO}/issues", {
        "title": QUEUE_TITLE + now().strftime("%Y%m%d-%H%M%S"),
        "body": body,
    })
    return log(
        "kaggle-child",
        x=value,
        verification="queued_only_not_executed",
        uptime_status="queued",
        queue_issue=issue.get("number"),
        queue_url=issue.get("html_url"),
    )


def kaggle_status():
    kernel = os.getenv("KAGGLE_KERNEL", "").strip()
    if not kernel or not os.getenv("KAGGLE_USERNAME") or not os.getenv("KAGGLE_KEY"):
        return log("kaggle-child", uptime_status="not_configured", verification=None,
                   failure="KAGGLE_KERNEL or Kaggle credentials missing")
    try:
        result = subprocess.run(
            ["kaggle", "kernels", "status", kernel],
            capture_output=True, text=True, timeout=45, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return log("kaggle-child", uptime_status="status_unknown", failure=type(exc).__name__)
    output = (result.stdout + "\n" + result.stderr)[:2000]
    upper = output.upper()
    if result.returncode:
        state = "status_unknown"
    elif "RUNNING" in upper:
        state = "running"
    elif "QUEUED" in upper:
        state = "queued"
    elif "COMPLETE" in upper or "SUCCEEDED" in upper:
        state = "completed_not_running"
    elif "ERROR" in upper or "FAILED" in upper:
        state = "failed_not_running"
    else:
        state = "not_active_or_unknown"
    row = log(
        "kaggle-child",
        uptime_status=state,
        verification="status_observed" if state != "status_unknown" else None,
        failure="Kaggle status command did not confirm state" if state == "status_unknown" else None,
        kaggle_kernel=kernel,
        status_excerpt=output,
    )
    if state in {"completed_not_running", "failed_not_running"}:
        collect_report(kernel)
    return row


def collect_report(kernel):
    try:
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                ["kaggle", "kernels", "output", kernel, "-p", folder,
                 "--file-pattern", r".*vorax-kaggle-report\.json$"],
                capture_output=True, text=True, timeout=120, check=False,
            )
            if result.returncode:
                return log("kaggle-child", uptime_status="report_unavailable",
                           failure=(result.stderr or result.stdout)[:200])
            report_file = next(Path(folder).glob("vorax-kaggle-report.json"), None)
            if not report_file:
                return log("kaggle-child", uptime_status="report_missing",
                           failure="Kaggle run returned no VORAX report")
            report = json.loads(report_file.read_text(encoding="utf-8"))
            gpu = report.get("gpu_telemetry") or {}
            log(
                "kaggle-child",
                uptime_status="run_report_received",
                verification="gpu_observed" if gpu.get("available") else "gpu_not_observed",
                gpu_devices=gpu.get("devices", []),
                failure=report.get("failure"),
                runtime_status=report.get("runtime"),
            )
            for task in report.get("tasks", []):
                for artifact in task.get("artifacts", []):
                    log(
                        "kaggle-child",
                        x=task.get("x"),
                        method=task.get("method"),
                        verification=task.get("verification"),
                        pr=task.get("pr"),
                        artifact_sha256=artifact.get("sha256"),
                        artifact_size_bytes=artifact.get("size_bytes"),
                        uptime_status="artifact_reported",
                        failure=task.get("failure"),
                        artifact_path=artifact.get("file"),
                    )
    except Exception as exc:
        log("kaggle-child", uptime_status="report_error", failure=type(exc).__name__ + ": " + str(exc)[:150])


def start_kaggle():
    kernel = os.getenv("KAGGLE_KERNEL", "").strip()
    if not kernel or not os.getenv("KAGGLE_USERNAME") or not os.getenv("KAGGLE_KEY"):
        return log("kaggle-child", uptime_status="not_configured", failure="Kaggle credentials/kernel missing")
    if not queue_items():
        return log("kaggle-child", uptime_status="idle_no_queue", verification="not_executed")
    try:
        subprocess.run([sys.executable, "scripts/prepare_kaggle_kernel.py"], check=True, timeout=30)
        timeout = min(max(int(os.getenv("KAGGLE_TIMEOUT_SECONDS", "42000")), 60), 43200)
        accelerator = os.getenv("KAGGLE_ACCELERATOR", "NvidiaTeslaT4")
        result = subprocess.run(
            ["kaggle", "kernels", "push", "-p", "kaggle/child",
             "--timeout", str(timeout), "--accelerator", accelerator],
            capture_output=True, text=True, timeout=180, check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return log("kaggle-child", uptime_status="start_failed", verification="not_executed",
                   failure=type(exc).__name__ + ": " + str(exc)[:180])
    output = (result.stdout + "\n" + result.stderr)[:1500]
    return log(
        "kaggle-child",
        uptime_status="start_requested" if result.returncode == 0 else "start_failed",
        verification="not_executed",
        failure=None if result.returncode == 0 else "Kaggle kernel push failed",
        kaggle_kernel=kernel,
        timeout_seconds=timeout,
        accelerator_requested=accelerator,
        status_excerpt=output,
    )


def start_campaign():
    if campaign():
        return log(uptime_status="campaign_already_active")
    started = now()
    ends = started + timedelta(days=7)
    payload = {
        "version": "vorax-campaign/v1",
        "status": "active",
        "started_at": stamp(started),
        "ends_at": stamp(ends),
        "scheduler": "GitHub Actions cron 17,47 * * * *",
    }
    issue = gh("POST", f"/repos/{REPO}/issues", {
        "title": CAMPAIGN_TITLE,
        "body": "<!-- VORAX_CAMPAIGN_V1 -->\n" + json.dumps(payload, indent=2),
    })
    return log(uptime_status="campaign_started", campaign_issue=issue.get("number"),
               campaign_url=issue.get("html_url"), campaign_end=stamp(ends))


def finalize_campaign(issue):
    payload = issue_payload(issue, "<!-- VORAX_CAMPAIGN_V1 -->")
    if not payload or not payload.get("ends_at"):
        return log(uptime_status="campaign_state_invalid", campaign_issue=issue.get("number"),
                   failure="Campaign issue has no valid end timestamp")
    try:
        end = datetime.fromisoformat(payload["ends_at"].replace("Z", "+00:00"))
    except ValueError:
        return log(uptime_status="campaign_state_invalid", campaign_issue=issue.get("number"),
                   failure="Campaign end timestamp is invalid")
    if now() < end:
        return log(uptime_status="campaign_active", campaign_issue=issue.get("number"),
                   campaign_end=stamp(end))
    gh("PATCH", f"/repos/{REPO}/issues/{issue['number']}", {
        "state": "closed",
        "body": issue.get("body", "") + "\n\nFinalized by GitHub Actions at " + stamp(),
    })
    return log(uptime_status="campaign_finalized", campaign_issue=issue.get("number"),
               campaign_end=stamp(end))


def heartbeat():
    koyeb = probe("koyeb-child", os.getenv("VORAX_KOYEB_URL", ""))
    probe("hf-backup-child", os.getenv("VORAX_HF_CHILD_URL", ""))
    if koyeb.get("uptime_status") == "healthy" and os.getenv("VORAX_KOYEB_CHILD_API_KEY"):
        health = koyeb.get("health") or {}
        if health.get("vault_configured"):
            try:
                request = Request(
                    os.getenv("VORAX_KOYEB_URL", "").rstrip("/") + "/v1/vault/sync",
                    data=b"{}",
                    headers={
                        "Content-Type": "application/json",
                        "x-child-id": "koyeb-child",
                        "x-child-key": os.environ["VORAX_KOYEB_CHILD_API_KEY"],
                    },
                    method="POST",
                )
                with urlopen(request, timeout=90) as response:
                    vault = json.loads(response.read(65536).decode("utf-8"))
                log("hf-vault", method="Home-to-Vault sync",
                    verification="mirrored_with_artifact_proofs",
                    uptime_status="sync_completed",
                    methods_mirrored=vault.get("methods_mirrored", 0),
                    artifacts_verified_and_mirrored=vault.get("artifacts_verified_and_mirrored", 0),
                    bytes_uploaded=vault.get("bytes_uploaded", 0),
                    vault_capacity_verified=vault.get("vault_capacity_verified", False))
            except Exception as exc:
                log("hf-vault", method="Home-to-Vault sync",
                    verification="not_synced", uptime_status="sync_failed",
                    failure=type(exc).__name__ + ": " + str(exc)[:160])
        else:
            log("hf-vault", method="Home-to-Vault sync",
                verification="not_synced", uptime_status="not_configured",
                failure="HF Vault token is not configured on Koyeb")
        try:
            request = Request(
                os.getenv("VORAX_KOYEB_URL", "").rstrip("/") + "/v1/devour/discover",
                data=json.dumps({"modality": "text", "limit": 5}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-child-id": "koyeb-child",
                    "x-child-key": os.environ["VORAX_KOYEB_CHILD_API_KEY"],
                },
                method="POST",
            )
            with urlopen(request, timeout=45) as response:
                discovery = json.loads(response.read(32768).decode("utf-8"))
            log("koyeb-child", method="HF teacher metadata discovery",
                verification="catalogued_metadata_only", uptime_status="healthy",
                models_found=discovery.get("models_found", 0), weights_downloaded=False)
        except Exception as exc:
            log("koyeb-child", method="HF teacher metadata discovery",
                verification="not_run", uptime_status="healthy", failure=type(exc).__name__)
    active = campaign()
    if active:
        finalize_campaign(active)
    status = kaggle_status()
    pending = queue_items()
    state = status.get("uptime_status")
    if pending and state in {"running", "queued", "start_requested", "status_unknown"}:
        log("kaggle-child", uptime_status="queue_waiting_for_session",
            verification="not_executed", failure="Kaggle session is not confirmed ready; X remains queued")
    elif pending and state in {"failed_not_running", "completed_not_running"}:
        log("kaggle-child", uptime_status="queued_for_next_session",
            verification="not_executed", failure="Kaggle session not active, queue X for next session; automatic retry paused to protect quota")
    elif pending:
        log("kaggle-child", uptime_status="queued_for_next_session",
            verification="not_executed", failure="Kaggle session not active, queue X for next session")
        start_kaggle()
    elif not pending:
        log("kaggle-child", uptime_status=state or "not_observed",
            verification="not_executed", failure="Kaggle session not active; no queued X to run")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=[
        "heartbeat", "start-campaign", "stop-campaign", "queue-x", "start-kaggle", "status"
    ], default="heartbeat")
    parser.add_argument("--x", default=os.getenv("VORAX_QUEUE_X", ""))
    args = parser.parse_args()
    if args.mode in {"heartbeat", "status"}:
        heartbeat()
    elif args.mode == "start-campaign":
        start_campaign()
    elif args.mode == "queue-x":
        queue_x(args.x)
        heartbeat()
    elif args.mode == "start-kaggle":
        status = kaggle_status()
        if status.get("uptime_status") not in {"running", "queued", "status_unknown"}:
            start_kaggle()
    elif args.mode == "stop-campaign":
        item = campaign()
        if item:
            gh("PATCH", f"/repos/{REPO}/issues/{item['number']}", {
                "state": "closed",
                "body": item.get("body", "") + "\n\nStopped manually at " + stamp(),
            })
        log(uptime_status="campaign_stopped" if item else "campaign_not_active")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(uptime_status="controller_error", failure=f"{type(exc).__name__}: {str(exc)[:220]}")
        raise
