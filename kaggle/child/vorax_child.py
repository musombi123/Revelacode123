#!/usr/bin/env python3
"""Ephemeral Kaggle worker. Uses the shared VORAX package; does not claim unsupported GPU media."""
from __future__ import annotations

import base64
import hashlib
import json
import re
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
        hf_model_token = secret("HF_MODEL_TOKEN")

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
        if hf_model_token:
            env["HF_MODEL_TOKEN"] = hf_model_token
        env["HF_HOME"] = "/kaggle/working/.cache/huggingface"
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

        pending_updates = []
        gpu_packages_installed = False
        for issue, queued in queue_issues(queue_token):
            value = str(queued.get("x", "")).strip()
            task = {
                "queue_issue": issue.get("number"),
                "x": value,
                "method": None,
                "verification": "not_executed",
                "provider": None,
                "pr": None,
                "artifacts": [],
                "failure": None,
            }
            queue_status = "worker_error_retry_later"
            try:
                if not value or len(value) > 12000:
                    raise ValueError("Queued X is invalid or exceeds the bounded input size")
                classification = classify_input_x(value, STORE.all())
                task["method"] = classification.get("task_type")

                if task["method"] == "3d":
                    task["verification"] = "unknown"
                    task["failure"] = "3D generation has no verified local adapter. No 3D artifact was produced."
                    queue_status = "unknown_3d_adapter_not_implemented"

                elif task["method"] in HEAVY:
                    if task["method"] in {"image", "logo"} and not gpu_packages_installed:
                        gpu_requirements = ROOT / "kaggle" / "child" / "requirements-gpu.txt"
                        subprocess.run(
                            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                             "--no-cache-dir", "-r", str(gpu_requirements)],
                            check=True, timeout=900,
                        )
                        gpu_packages_installed = True

                    result = request_json("POST", "/vorax/x", child_key, {"x": value})
                    answer = result.get("answer") if isinstance(result, dict) else None
                    task["result_status"] = result.get("status") if isinstance(result, dict) else "invalid_response"

                    if (
                        isinstance(answer, dict)
                        and answer.get("status") == "verified"
                        and answer.get("provider") == "kaggle-local-diffusers"
                        and isinstance(answer.get("gpu_telemetry"), dict)
                        and answer["gpu_telemetry"].get("gpu_used") is True
                    ):
                        artifact_id = answer.get("artifact")
                        if not isinstance(artifact_id, str) or not re.fullmatch(r"[a-f0-9]{32}\.png", artifact_id):
                            raise RuntimeError("Kaggle GPU returned an invalid artifact identifier")
                        artifact_root = Path(env["LEGEND_ARTIFACT_DIR"]).resolve()
                        artifact_path = (artifact_root / artifact_id).resolve()
                        if artifact_root not in artifact_path.parents or not artifact_path.is_file():
                            raise RuntimeError("Kaggle GPU artifact is missing or outside the artifact root")
                        from PIL import Image, ImageStat
                        with Image.open(artifact_path) as image:
                            image.verify()
                        with Image.open(artifact_path) as image:
                            image.load()
                            if image.format != "PNG":
                                raise RuntimeError("Generated artifact is not a PNG")
                            extrema = ImageStat.Stat(image.convert("RGB")).extrema
                            if all(low == high for low, high in extrema):
                                raise RuntimeError("Generated PNG is blank")
                            dimensions = {"width": image.width, "height": image.height}
                        digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
                        size = artifact_path.stat().st_size
                        if answer.get("sha256") != digest or answer.get("size_bytes") != size:
                            raise RuntimeError("Independent artifact hash or byte-size check did not match the runtime report")
                        telemetry = answer["gpu_telemetry"]
                        if not str(telemetry.get("device", "")).startswith("cuda:"):
                            raise RuntimeError("The reported model device is not CUDA")
                        if not telemetry.get("gpu_name"):
                            raise RuntimeError("The actual CUDA GPU name was not recorded")

                        task.update({
                            "verification": "local_png_verified_pending_vault",
                            "provider": "kaggle-local-diffusers",
                            "model_id": answer.get("model_id"),
                            "gpu_telemetry": telemetry,
                            "execution_time_ms": answer.get("elapsed_ms"),
                            "artifact": {
                                "filename": artifact_id,
                                "local_path": str(artifact_path),
                                "format": "png",
                                "size_bytes": size,
                                "sha256": digest,
                                **dimensions,
                                "independent_verification": "passed",
                            },
                        })
                        queue_status = "verified_local_artifact_pending_vault"

                    else:
                        reason = (
                            answer.get("reason") if isinstance(answer, dict)
                            else result.get("reason") or result.get("message") if isinstance(result, dict)
                            else "No verified local Kaggle image result was returned"
                        )
                        task["verification"] = "blocked_or_unknown_no_artifact"
                        task["failure"] = str(reason or "Local Kaggle modality adapter unavailable")[:500]
                        task["provider"] = answer.get("provider") if isinstance(answer, dict) else None
                        if task["method"] == "image" or task["method"] == "logo":
                            queue_status = "blocked_local_gpu_adapter"
                        else:
                            queue_status = "unknown_local_adapter_not_implemented"

                else:
                    result = request_json("POST", "/vorax/x", child_key, {"x": value})
                    task["verification"] = (
                        "shared_runtime_response_received_not_home_merged"
                        if result.get("status") == "verified" else "unknown"
                    )
                    task["result_status"] = result.get("status")
                    task["result_summary"] = {
                        key: result.get(key) for key in ("template_id", "learning", "discovery")
                        if key in result
                    }
                    learning = result.get("learning") or {}
                    task["pr"] = learning.get("pull_request") or learning.get("pr")
                    queue_status = "processed_pending_home_validation" if result.get("status") == "verified" else "unknown_no_verified_method"

                report["tasks"].append(task)
                pending_updates.append((issue, queued, queue_status, task))

            except Exception as exc:
                task["failure"] = type(exc).__name__ + ": " + str(exc)[:300]
                task["verification"] = "failed"
                report["tasks"].append(task)
                pending_updates.append((issue, queued, "worker_error_retry_later", task))

        if hf_vault:
            try:
                report["vault_sync"] = request_json("POST", "/v1/vault/sync", child_key, {})
            except Exception as exc:
                report["vault_sync"] = {"status": "failed", "failure": type(exc).__name__}
        else:
            report["vault_sync"] = {"status": "not_configured", "verification": "not_synced"}

        vault_proofs = (
            report.get("vault_sync", {}).get("artifact_proofs", [])
            if isinstance(report.get("vault_sync"), dict) else []
        )
        for issue, queued, queue_status, task in pending_updates:
            artifact = task.get("artifact")
            if artifact and queue_status == "verified_local_artifact_pending_vault":
                proof = next(
                    (item for item in vault_proofs if item.get("sha256") == artifact["sha256"]),
                    None,
                )
                if proof and proof.get("path") == "artifacts/images/" + artifact["sha256"] + ".png":
                    artifact["vault_path"] = proof["path"]
                    task["verification"] = "png_verified_and_vaulted"
                    queue_status = "verified_artifact_vaulted"
                else:
                    task["verification"] = (
                        "png_verified_vault_sync_failed"
                        if report.get("vault_sync", {}).get("status") == "failed"
                        else "png_verified_but_vault_proof_missing"
                    )
                    queue_status = "verified_local_artifact_not_vaulted"
            try:
                update_queue(issue, queued, queue_token, queue_status, task)
            except Exception as exc:
                task["queue_update_failure"] = type(exc).__name__ + ": " + str(exc)[:180]

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
