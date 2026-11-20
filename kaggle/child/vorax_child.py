#!/usr/bin/env python3
"""Ephemeral Kaggle worker. Uses the shared VORAX package; does not claim unsupported GPU media."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = os.getenv("LEGEND_HOME_REPO", "musombi123/Revelacode123")
BRANCH = "main"
ROOT = Path("/kaggle/working/vorax-home")
REPORT = Path("/kaggle/working/vorax-kaggle-report.json")
API = "http://127.0.0.1:8765"
MARKER = "<!-- VORAX_X_QUEUE_V1 -->"
HEAVY = {"image", "logo", "music", "video", "speech", "audio", "3d"}


def secret(name: str, required: bool = False) -> str:
    try:
        from kaggle_secrets import UserSecretsClient
        value = UserSecretsClient().get_secret(name) or ""
    except Exception:
        value = ""
    value = value.strip()
    if required and not value:
        raise RuntimeError(f"Kaggle secret {name} is required")
    return value


def github(method: str, path: str, token: str, payload=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "VORAX-Kaggle-Child",
    }
    data = json.dumps(payload).encode() if payload is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request("https://api.github.com" + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        return json.loads(raw.decode()) if raw else {}


def clone_home(read_token: str):
    if ROOT.exists():
        subprocess.run(["rm", "-rf", str(ROOT)], check=True)
    encoded = base64.b64encode(("x-access-token:" + read_token).encode()).decode()
    env = os.environ.copy()
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
    env["GIT_CONFIG_VALUE_0"] = "AUTHORIZATION: basic " + encoded
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", BRANCH,
         "https://github.com/" + REPO + ".git", str(ROOT)],
        check=True, timeout=180, env=env,
    )


def gpu_telemetry():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,uuid,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20, check=True,
        )
        devices = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return {"available": bool(devices), "devices": devices, "source": "nvidia-smi"}
    except Exception as exc:
        return {"available": False, "devices": [], "source": "nvidia-smi",
                "failure": type(exc).__name__}


def request_json(method: str, path: str, child_key: str, payload=None):
    data = json.dumps(payload or {}).encode()
    request = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-child-id": "kaggle-child",
            "x-child-key": child_key,
        },
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def start_runtime(env):
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "vorax.api:app", "--host", "127.0.0.1", "--port", "8765"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        if server.poll() is not None:
            raise RuntimeError("Shared VORAX runtime exited before health became ready")
        try:
            with urllib.request.urlopen(API + "/health", timeout=3) as response:
                health = json.loads(response.read().decode())
                if response.status == 200 and health.get("engine") == "VORAX":
                    return server, health
        except Exception:
            time.sleep(2)
    server.terminate()
    raise RuntimeError("Shared VORAX /health did not become ready")


def queue_issues(queue_token: str):
    issues = []
    for page in range(1, 6):
        batch = github("GET", f"/repos/{REPO}/issues?state=open&per_page=100&page={page}", queue_token)
        issues.extend(batch)
        if len(batch) < 100:
            break
    found = []
    for issue in issues:
        if "pull_request" in issue or not str(issue.get("title", "")).startswith("[VORAX-X]"):
            continue
        body = issue.get("body") or ""
        if MARKER not in body:
            continue
        raw = body.split(MARKER, 1)[1].split("\n\nDo not put", 1)[0].strip()
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if item.get("version") == "vorax-x-queue/v1" and item.get("status") == "queued":
            found.append((issue, item))
    return found


def update_queue(issue, payload, queue_token, status, result=None):
    payload["status"] = status
    payload["last_attempt_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload["worker"] = "kaggle-child"
    if result:
        payload["result_summary"] = result
    body = MARKER + "\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    github("PATCH", f"/repos/{REPO}/issues/{issue['number']}", queue_token,
           {"body": body})


def main():
    report = {
        "child": "kaggle-child",
        "runtime": "starting",
        "gpu_telemetry": gpu_telemetry(),
        "tasks": [],
        "vault_sync": {"status": "not_attempted"},
        "failure": None,
    }
    server = None
    try:
        if not report["gpu_telemetry"]["available"]:
            report["runtime"] = "gpu_not_observed"
            report["failure"] = "No NVIDIA GPU telemetry was observed; no GPU work was claimed"
            raise RuntimeError(report["failure"])

        read_token = secret("VORAX_HOME_READ_TOKEN", required=True)
        queue_token = secret("VORAX_HOME_WRITE_TOKEN", required=True)
        child_key = secret("VORAX_KAGGLE_CHILD_API_KEY", required=True)
        proposal_token = secret("VORAX_CHILD_PROPOSAL_TOKEN") or queue_token
        hf_vault = secret("HF_VAULT_TOKEN")
        teacher_key = secret("LEGEND_TEACHER_API_KEY")
        teacher_model = secret("LEGEND_TEACHER_MODEL")
        hf_inference = secret("HF_INFERENCE_TOKEN")

        clone_home(read_token)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(ROOT / "requirements.txt")],
            check=True, timeout=600,
        )
        env = os.environ.copy()
        env.update({
            "PYTHONPATH": str(ROOT),
            "PORT": "8765",
            "VORAX_CHILD_ID": "kaggle-child",
            "VORAX_CHILD_ROLE": "kaggle",
            "VORAX_CHILD_API_KEY": child_key,
            "LEGEND_HOME_REPO": REPO,
            "LEGEND_HOME_BRANCH": "main",
            "LEGEND_HOME_READ_TOKEN": read_token,
            "LEGEND_DB_PATH": "/kaggle/working/legend.sqlite3",
            "LEGEND_ARTIFACT_DIR": "/kaggle/working/artifacts",
            "LEGEND_AUTO_LEARN": "true",
            "HF_VAULT_REPO": "Musombi/revelacode-vorax-vault",
        })
        if proposal_token:
            env["LEGEND_GITHUB_TOKEN"] = proposal_token
        if hf_vault:
            env["HF_VAULT_TOKEN"] = hf_vault
        if hf_inference:
            env["HF_INFERENCE_TOKEN"] = hf_inference
        if teacher_key and teacher_model:
            env["LEGEND_TEACHER_API_KEY"] = teacher_key
            env["LEGEND_TEACHER_MODEL"] = teacher_model
            teacher_base = secret("LEGEND_TEACHER_BASE_URL")
            if teacher_base:
                env["LEGEND_TEACHER_BASE_URL"] = teacher_base

        server, health = start_runtime(env)
        report["runtime"] = "shared_vorax_healthy"
        report["health"] = health

        discovery = request_json("POST", "/v1/devour/discover", child_key,
                                 {"modality": "text", "limit": 5})
        report["discovery"] = {
            "status": discovery.get("status"),
            "models_found": discovery.get("models_found", 0),
            "weights_downloaded": discovery.get("weights_downloaded", False),
        }

        from vorax.api import STORE, classify_input_x

        for issue, queued in queue_issues(queue_token):
            value = str(queued.get("x", "")).strip()
            task = {
                "queue_issue": issue.get("number"),
                "x": value,
                "method": None,
                "verification": "not_executed",
                "pr": None,
                "artifacts": [],
                "failure": None,
            }
            try:
                if not value or len(value) > 12000:
                    raise ValueError("Queued X is invalid or exceeds the bounded input size")
                classification = classify_input_x(value, STORE.all())
                task["method"] = classification.get("task_type")
                if task["method"] in HEAVY:
                    modality = (
                        "image" if task["method"] in {"image", "logo"} else
                        "audio" if task["method"] in {"speech", "audio"} else
                        "text" if task["method"] == "3d" else
                        task["method"]
                    )
                    metadata = request_json("POST", "/v1/devour/discover", child_key,
                                             {"modality": modality, "limit": 10})
                    task["verification"] = "metadata_only_not_media_execution"
                    task["gpu_telemetry_available"] = report["gpu_telemetry"]["available"]
                    task["failure"] = (
                        "Local GPU inference adapter for this modality is not yet installed. "
                        "Model metadata was discovered only; no PNG/WAV/MP4 was generated."
                    )
                    task["models_found"] = metadata.get("models_found", 0)
                    update_queue(issue, queued, queue_token, "blocked_local_gpu_adapter", task)
                else:
                    result = request_json("POST", "/vorax/x", child_key, {"x": value})
                    task["verification"] = "shared_runtime_response_received_not_home_merged"
                    task["result_status"] = result.get("status")
                    task["result_summary"] = {
                        key: result.get(key) for key in ("template_id", "learning", "discovery")
                        if key in result
                    }
                    learning = result.get("learning") or {}
                    task["pr"] = learning.get("pull_request") or learning.get("pr")
                    update_queue(issue, queued, queue_token, "processed_pending_home_validation", task)
                report["tasks"].append(task)
            except Exception as exc:
                task["failure"] = type(exc).__name__ + ": " + str(exc)[:180]
                report["tasks"].append(task)
                try:
                    update_queue(issue, queued, queue_token, "worker_error_retry_later", task)
                except Exception:
                    pass

        if hf_vault:
            try:
                report["vault_sync"] = request_json("POST", "/v1/vault/sync", child_key, {})
            except Exception as exc:
                report["vault_sync"] = {"status": "failed", "failure": type(exc).__name__}
        else:
            report["vault_sync"] = {"status": "not_configured", "verification": "not_synced"}

    except Exception as exc:
        report["failure"] = type(exc).__name__ + ": " + str(exc)[:250]
        print("VORAX Kaggle Child:", report["failure"])
    finally:
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Report written:", REPORT)
    if report["failure"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
