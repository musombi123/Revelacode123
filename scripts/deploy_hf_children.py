#!/usr/bin/env python3
"""Deploy the two public, app-authenticated, weight-free VORAX Hugging Face Docker Spaces.

This script never creates or mutates the existing Dataset Vault. It checks the
Vault read-only before deployment. It does not request GPU hardware or run a
remote inference smoke test unless explicitly selected.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub.utils import HfHubHTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OWNER = "Musombi"
TEACHER_REPO = f"{OWNER}/vorax-teacher-service"
RUNTIME_REPO = f"{OWNER}/vorax-runtime-service"
VAULT_REPO = "Musombi/revelacode-vorax-vault"
HOME_REPO = "musombi123/Revelacode123"
CHILD_ID = "kaggle-child"

REQUIRED_ENV = (
    "HF_SPACE_TOKEN",
    "HF_TOKEN",
    "HF_READ_TOKEN",
    "VORAX_KAGGLE_CHILD_API_KEY",
    "VORAX_HOME_READ_TOKEN",
    "VORAX_HOME_WRITE_TOKEN",
    "HF_VAULT_TOKEN",
    "VORAX_TEACHER_ATTESTATION_KEY",
)


def require_env() -> dict[str, str]:
    missing = [name for name in REQUIRED_ENV if not os.getenv(name, "").strip()]
    if missing:
        raise SystemExit("Missing environment variables (values are never printed): " + ", ".join(missing))
    return {name: os.environ[name].strip() for name in REQUIRED_ENV}


def assert_weight_free(path: Path) -> None:
    forbidden_suffixes = {".safetensors", ".pt", ".pth", ".ckpt", ".gguf", ".onnx", ".model", ".bin", ".h5", ".pb", ".tflite", ".msgpack", ".weights"}
    for item in path.rglob("*"):
        rel = item.relative_to(path).as_posix().lower()
        if "hf-model/" in rel or rel.endswith("/hf-model") or rel == "hf-model":
            raise RuntimeError(f"Forbidden hf-model path in Space package: {rel}")
        if item.is_file() and (item.suffix.lower() in forbidden_suffixes or item.name.lower() == "pytorch_model.bin"):
            raise RuntimeError(f"Forbidden model weight file in Space package: {rel}")
        if item.is_file() and item.name.lower() in {"requirements.txt", "pyproject.toml"}:
            text = item.read_text(encoding="utf-8", errors="replace").lower()
            if "diffusers" in text or "transformers" in text:
                raise RuntimeError(f"Local model-generation dependency found in Space package: {rel}")
        if item.is_file() and item.suffix.lower() == ".py":
            source = item.read_text(encoding="utf-8", errors="replace").lower()
            forbidden = ("diffusers", "snapshot_download", "transformers.pipeline", ".from_pretrained(")
            if any(token in source for token in forbidden):
                raise RuntimeError(f"Local model loading code found in Space package: {rel}")


def inspect_existing_vault(token: str) -> dict:
    # Import only after deployment dependencies are present; inspect_vault is read-only.
    os.environ["HF_VAULT_TOKEN"] = token
    os.environ["HF_VAULT_REPO"] = VAULT_REPO
    from vorax.vault import inspect_vault

    proof = inspect_vault()
    if proof.get("repo_type") != "dataset" or proof.get("private") is not True:
        raise RuntimeError("Existing Vault is not verified as a private Dataset; refusing deployment.")
    if proof.get("status") != "verified":
        raise RuntimeError("Existing Vault structure is incomplete; no replacement will be created.")
    if proof.get("weights_present") is not False or proof.get("architecture_configs_present") is not False:
        raise RuntimeError("Vault weight/config audit did not pass.")
    return proof


def ensure_public_docker_space(api: HfApi, repo_id: str) -> None:
    try:
        api.create_repo(
            repo_id=repo_id,
            repo_type="space",
            space_sdk="docker",
            private=False,
            exist_ok=True,
        )
        info = api.repo_info(repo_id=repo_id, repo_type="space")
    except Exception as exc:
        # Hugging Face can reject personal Docker Spaces when the account is not eligible.
        # Do not switch to paid hardware or a different resource type automatically.
        raise RuntimeError(
            f"Could not create/open {repo_id} as a Docker Space ({type(exc).__name__}). "
            "No paid hardware or alternative resource was requested; deployment stopped."
        ) from exc

    if getattr(info, "private", True) is not False:
        raise RuntimeError(f"{repo_id} is private; the Kaggle Child cannot call its API without an additional Space-access token. Refusing silent access-policy changes.")
    sdk = getattr(info, "sdk", None)
    if sdk and str(sdk).lower() != "docker":
        raise RuntimeError(f"{repo_id} is an existing {sdk!r} Space, not Docker; refusing to repurpose it.")


def set_secrets(api: HfApi, repo_id: str, values: dict[str, str]) -> None:
    for name, value in values.items():
        if not value:
            continue
        api.add_space_secret(repo_id=repo_id, key=name, value=value)


def make_teacher_context(target: Path) -> None:
    source = ROOT / "deploy" / "hf-teacher"
    shutil.copy2(source / "README.md", target / "README.md")
    shutil.copy2(source / "Dockerfile", target / "Dockerfile")
    shutil.copy2(source / "requirements.txt", target / "requirements.txt")
    shutil.copy2(source / "app.py", target / "app.py")
    shutil.copy2(ROOT / "vorax" / "hf_teacher.py", target / "hf_teacher.py")
    assert_weight_free(target)


def make_runtime_context(target: Path) -> None:
    source = ROOT / "deploy" / "hf-runtime"
    shutil.copy2(source / "README.md", target / "README.md")
    shutil.copy2(source / "Dockerfile", target / "Dockerfile")
    shutil.copy2(source / "requirements.txt", target / "requirements.txt")
    shutil.copy2(source / "app.py", target / "app.py")

    shutil.copytree(
        ROOT / "vorax",
        target / "vorax",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "kaggle_gpu.py"),
    )
    assert_weight_free(target)
    reqs = (target / "requirements.txt").read_text(encoding="utf-8").lower()
    if "diffusers" in reqs or "transformers" in reqs:
        raise RuntimeError("Runtime dependencies must not install Diffusers/Transformers.")


def upload_space(api: HfApi, repo_id: str, folder: Path, message: str) -> None:
    api.upload_folder(
        repo_id=repo_id,
        repo_type="space",
        folder_path=str(folder),
        path_in_repo="",
        commit_message=message,
    )


def request_json(url: str, *, body: dict | None = None, headers: dict | None = None, timeout: int = 30):
    data = None
    req_headers = {"Accept": "application/json", "User-Agent": "VORAX-HF-Deployment/1.0"}
    if headers:
        req_headers.update(headers)
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def wait_health(url: str, timeout_seconds: int) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not checked"
    while time.monotonic() < deadline:
        try:
            status, payload = request_json(url, timeout=12)
            if status == 200 and payload.get("status") == "ok":
                return payload
            last_error = f"HTTP {status}: unexpected health body"
        except Exception as exc:
            last_error = type(exc).__name__
        time.sleep(8)
    raise RuntimeError(f"Health check did not pass for {url}: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--health-wait-seconds", type=int, default=240)
    parser.add_argument(
        "--smoke-remote-inference",
        action="store_true",
        help="Make one real Teacher inference request. This is deliberately opt-in and may consume provider quota/credits.",
    )
    parser.add_argument("--teacher-model", default=os.getenv("VORAX_TEACHER_MODEL", ""))
    args = parser.parse_args()

    if args.health_wait_seconds < 0 or args.health_wait_seconds > 1800:
        raise SystemExit("--health-wait-seconds must be between 0 and 1800 seconds.")
    env = require_env()

    # Dataset inspection is read-only; this script never calls create_repo for repo_type=dataset.
    vault_proof = inspect_existing_vault(env["HF_VAULT_TOKEN"])

    teacher_url = "https://musombi-vorax-teacher-service.hf.space"
    runtime_url = "https://musombi-vorax-runtime-service.hf.space"

    api = HfApi(token=env["HF_SPACE_TOKEN"])
    ensure_public_docker_space(api, TEACHER_REPO)
    ensure_public_docker_space(api, RUNTIME_REPO)

    teacher_secrets = {
        "HF_TOKEN": env["HF_TOKEN"],
        "HF_READ_TOKEN": env["HF_READ_TOKEN"],
        "VORAX_CHILD_ID": CHILD_ID,
        "VORAX_KAGGLE_CHILD_API_KEY": env["VORAX_KAGGLE_CHILD_API_KEY"],
        "VORAX_TEACHER_ATTESTATION_KEY": env["VORAX_TEACHER_ATTESTATION_KEY"],
    }
    runtime_secrets = {
        "VORAX_CHILD_ID": CHILD_ID,
        "VORAX_CHILD_ROLE": "hf-runtime",
        "VORAX_KAGGLE_CHILD_API_KEY": env["VORAX_KAGGLE_CHILD_API_KEY"],
        "VORAX_HOME_REPO": HOME_REPO,
        "VORAX_HOME_BRANCH": "main",
        "VORAX_HOME_READ_TOKEN": env["VORAX_HOME_READ_TOKEN"],
        "VORAX_HOME_WRITE_TOKEN": env["VORAX_HOME_WRITE_TOKEN"],
        "HF_VAULT_TOKEN": env["HF_VAULT_TOKEN"],
        "HF_VAULT_REPO": VAULT_REPO,
        "HF_INFERENCE_TOKEN": env["HF_TOKEN"],
        "VORAX_TEACHER_ATTESTATION_KEY": env["VORAX_TEACHER_ATTESTATION_KEY"],
        "VORAX_TEACHER_URL": teacher_url,
        "VORAX_TEACHER_MODEL": args.teacher_model.strip(),
    }
    set_secrets(api, TEACHER_REPO, teacher_secrets)
    set_secrets(api, RUNTIME_REPO, runtime_secrets)

    with tempfile.TemporaryDirectory(prefix="vorax-hf-children-") as temp:
        base = Path(temp)
        teacher_dir = base / "teacher"
        runtime_dir = base / "runtime"
        teacher_dir.mkdir()
        runtime_dir.mkdir()
        make_teacher_context(teacher_dir)
        make_runtime_context(runtime_dir)
        upload_space(api, TEACHER_REPO, teacher_dir, "Deploy weight-free VORAX Teacher Service")
        upload_space(api, RUNTIME_REPO, runtime_dir, "Deploy weight-free VORAX Runtime Service")

    report = {
        "vault": vault_proof,
        "teacher": {"repo": TEACHER_REPO, "url": teacher_url, "access": "public_endpoint_child_auth", "uploaded": True, "live": False},
        "runtime": {"repo": RUNTIME_REPO, "url": runtime_url, "access": "public_endpoint_child_auth", "uploaded": True, "live": False},
        "weights_in_space_payloads": 0,
        "paid_hardware_requested": False,
        "go_no_go": "BLOCKED",
    }

    if args.health_wait_seconds:
        teacher_health = wait_health(teacher_url + "/health", args.health_wait_seconds)
        runtime_health = wait_health(runtime_url + "/health", args.health_wait_seconds)
        if teacher_health.get("metadata_token_configured") is not True:
            raise RuntimeError("Teacher is live but HF_READ_TOKEN for metadata discovery is missing.")
        if teacher_health.get("attestation_configured") is not True:
            raise RuntimeError("Teacher is live but its private Runtime attestation key is not configured.")
        if runtime_health.get("required_secrets", {}).get("teacher_attestation_key") is not True:
            raise RuntimeError("Runtime is live but its Teacher attestation key is missing.")
        report["teacher"]["health"] = teacher_health
        report["teacher"]["live"] = True
        report["runtime"]["health"] = runtime_health
        report["runtime"]["live"] = True

        status, discovery = request_json(teacher_url + "/teacher/discover?query=language&limit=3")
        report["teacher"]["discovery"] = {
            "http_status": status,
            "models_found": discovery.get("models_found"),
            "metadata_only": discovery.get("status") == "metadata_only",
            "weights_downloaded": discovery.get("weights_downloaded"),
        }
        if status != 200 or not discovery.get("models"):
            raise RuntimeError("Teacher metadata discovery smoke test did not return model metadata.")

        status, runtime_smoke = request_json(
            runtime_url + "/vorax/x",
            body={"x": "hello, who are you", "child_id": CHILD_ID, "api_key": env["VORAX_KAGGLE_CHILD_API_KEY"]},
            timeout=45,
        )
        report["runtime"]["input_x_smoke"] = {
            "http_status": status,
            "status": runtime_smoke.get("status"),
            "method": runtime_smoke.get("method"),
            "verification": runtime_smoke.get("verification"),
        }
        if status != 200 or runtime_smoke.get("status") != "verified":
            raise RuntimeError("Runtime Input X smoke test did not return verified.")

        if runtime_health.get("ready") is not True:
            raise RuntimeError("Runtime is live but not ready; inspect its Vault/Home secret health fields.")

    if args.smoke_remote_inference:
        if not args.teacher_model.strip():
            raise SystemExit("--smoke-remote-inference requires --teacher-model.")
        status, inference = request_json(
            teacher_url + "/teacher/ask",
            body={"prompt": "Give one short deterministic method for reversing a text string. Do not claim tests were run.", "model_id": args.teacher_model.strip()},
            headers={"x-child-id": CHILD_ID, "x-child-key": env["VORAX_KAGGLE_CHILD_API_KEY"]},
            timeout=120,
        )
        report["teacher"]["remote_inference_smoke"] = {
            "http_status": status,
            "model_id": inference.get("model_id"),
            "provider_used": inference.get("provider_used"),
            "remote_inference": inference.get("remote_inference"),
            "kaggle_t4_used": inference.get("kaggle_t4_used"),
            "weights_downloaded": inference.get("weights_downloaded"),
        }
        if status != 200 or inference.get("remote_inference") is not True or inference.get("kaggle_t4_used") is not False:
            raise RuntimeError("Teacher remote inference smoke test did not prove the required provenance.")


    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HfHubHTTPError, urllib.error.HTTPError) as exc:
        print(f"HF deployment failed: {type(exc).__name__}. No secret values were printed.", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        print(f"Deployment stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
