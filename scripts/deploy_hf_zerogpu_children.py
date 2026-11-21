#!/usr/bin/env python3
"""Deploy VORAX Teacher + Runtime to free Gradio/ZeroGPU Spaces.

Live publication is strictly gated by the existing VORAX release policy:
a published GitHub Release tag, exact tagged SHA in main history, and a
successful latest 'verify' check for that exact SHA. Local tests are run as
additional evidence, never as a substitute for GitHub's release gate.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub.utils import HfHubHTTPError, RepositoryNotFoundError

ROOT = Path(__file__).resolve().parents[1]
OWNER = "Musombi"
TEACHER_REPO = f"{OWNER}/vorax-teacher-service"
RUNTIME_REPO = f"{OWNER}/vorax-runtime-service"
VAULT_REPO = "Musombi/revelacode-vorax-vault"
HOME_REPO = "musombi123/Revelacode123"
CHILD_ID = "kaggle-child"
TEACHER_URL = "https://musombi-vorax-teacher-service.hf.space"
RUNTIME_URL = "https://musombi-vorax-runtime-service.hf.space"
ZERO_GPU = "zero-a10g"

REQUIRED_SECRETS = (
    "HF_SPACE_TOKEN",
    "HF_TOKEN",
    "HF_READ_TOKEN",
    "VORAX_KAGGLE_CHILD_API_KEY",
    "VORAX_HOME_READ_TOKEN",
    "VORAX_HOME_WRITE_TOKEN",
    "HF_VAULT_TOKEN",
    "VORAX_TEACHER_ATTESTATION_KEY",
)
WEIGHT_SUFFIXES = {
    ".safetensors", ".pt", ".pth", ".ckpt", ".gguf", ".onnx",
    ".model", ".bin", ".h5", ".pb", ".tflite", ".msgpack", ".weights",
}


def run(
    command: list[str],
    *,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=capture,
        check=False,
        env=env,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)} {detail}")
    return result


def verify_release_gate(commit_sha: str) -> dict:
    """Invoke the repository's unchanged exact-SHA release verifier."""
    release_tag = os.getenv("VORAX_RELEASE_TAG", "").strip()
    if not release_tag:
        raise RuntimeError(
            "VORAX_RELEASE_TAG is required. Deployment cannot proceed without an existing published release."
        )
    token = (os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or "").strip()
    if not token:
        token = getpass.getpass("GitHub read token for exact-SHA release verification (hidden): ").strip()
    if not token:
        raise RuntimeError("GitHub release-verification token was not supplied.")

    env = os.environ.copy()
    env.update({
        "GITHUB_REPOSITORY": HOME_REPO,
        "GITHUB_REF_TYPE": "tag",
        "GITHUB_REF_NAME": release_tag,
        "GITHUB_SHA": commit_sha,
        "GH_TOKEN": token,
    })
    result = run(
        [sys.executable, str(ROOT / "scripts" / "require_green_release.py")],
        capture=True,
        env=env,
    )
    try:
        proof = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Release verifier did not return its expected JSON proof.") from exc
    if proof.get("status") != "release_gate_passed" or proof.get("release_sha", "").lower() != commit_sha.lower():
        raise RuntimeError("Existing exact-SHA release gate did not approve this deployment.")
    return proof


def local_release_gate() -> tuple[str, dict]:
    """Require clean current main, rerun tests, then require the GitHub release gate."""
    branch = run(["git", "branch", "--show-current"], capture=True).stdout.strip()
    if branch != "main":
        raise RuntimeError(f"Deployment requires branch 'main'; current branch is {branch!r}")

    run(["git", "fetch", "origin"])
    local_sha = run(["git", "rev-parse", "HEAD"], capture=True).stdout.strip()
    remote_sha = run(["git", "rev-parse", "origin/main"], capture=True).stdout.strip()
    if not local_sha or local_sha != remote_sha:
        raise RuntimeError("Local main is not exactly equal to origin/main; fast-forward before deployment.")

    status = run(["git", "status", "--porcelain", "--untracked-files=all"], capture=True).stdout
    if status.strip():
        raise RuntimeError("Working tree is not clean; commit or discard local changes before deployment.")

    print("\n=== LOCAL TEST SUITE (additional check, not CI replacement) ===", flush=True)
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    status_after = run(["git", "status", "--porcelain", "--untracked-files=all"], capture=True).stdout
    if status_after.strip():
        raise RuntimeError("Tests changed tracked/untracked repository files; refusing deployment.")

    release_proof = verify_release_gate(local_sha)
    print(json.dumps({
        "status": "release_gate_and_local_tests_passed",
        "commit": local_sha,
        "branch": branch,
        "working_tree_clean": True,
        "local_test_suite": "passed",
        "github_actions_verified": True,
        "release_gate": release_proof,
    }, indent=2))
    return local_sha, release_proof


def prompt_secrets() -> dict[str, str]:
    values: dict[str, str] = {}
    for name in REQUIRED_SECRETS:
        value = os.getenv(name, "").strip()
        if not value:
            value = getpass.getpass(f"Enter {name} (hidden): ").strip()
        if not value:
            raise RuntimeError(f"Required credential {name} was not supplied.")
        values[name] = value
    if len(values["VORAX_TEACHER_ATTESTATION_KEY"]) < 32:
        raise RuntimeError("VORAX_TEACHER_ATTESTATION_KEY must contain at least 32 characters.")
    return values


def assert_weight_free(folder: Path) -> None:
    for item in folder.rglob("*"):
        relative = item.relative_to(folder).as_posix().lower()
        if "hf-model/" in relative or relative == "hf-model":
            raise RuntimeError(f"Forbidden hf-model path in package: {relative}")
        if item.is_file() and item.suffix.lower() in WEIGHT_SUFFIXES:
            raise RuntimeError(f"Forbidden model weight file in package: {relative}")
        if item.is_file() and item.name.lower() in {"requirements.txt", "pyproject.toml"}:
            text = item.read_text(encoding="utf-8", errors="replace").lower()
            if "diffusers" in text or "transformers" in text:
                raise RuntimeError(f"Forbidden local model dependency in package: {relative}")
        if item.is_file() and item.suffix.lower() == ".py":
            source = item.read_text(encoding="utf-8", errors="replace").lower()
            forbidden = ("diffusers", "snapshot_download", "transformers.pipeline", ".from_pretrained(")
            if any(marker in source for marker in forbidden):
                raise RuntimeError(f"Forbidden local model loader in package: {relative}")


def build_teacher_package(target: Path) -> None:
    source = ROOT / "deploy" / "hf-teacher-gradio"
    for filename in ("README.md", "requirements.txt", "app.py"):
        shutil.copy2(source / filename, target / filename)
    shutil.copy2(ROOT / "vorax" / "hf_teacher.py", target / "hf_teacher.py")
    assert_weight_free(target)


def build_runtime_package(target: Path) -> None:
    source = ROOT / "deploy" / "hf-runtime-gradio"
    for filename in ("README.md", "requirements.txt", "packages.txt", "app.py"):
        shutil.copy2(source / filename, target / filename)
    shutil.copytree(
        ROOT / "vorax",
        target / "vorax",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "kaggle_gpu.py"),
    )
    if (target / "templates").exists():
        raise RuntimeError("Runtime package must never contain Home's method templates.")
    assert_weight_free(target)


def inspect_existing_vault(token: str) -> dict:
    """Read-only verification. Never create, convert, overwrite or replace the Vault."""
    os.environ["HF_VAULT_TOKEN"] = token
    os.environ["HF_VAULT_REPO"] = VAULT_REPO
    from vorax.vault import inspect_vault

    proof = inspect_vault()
    if (
        proof.get("status") != "verified"
        or proof.get("repo_type") != "dataset"
        or proof.get("private") is not True
        or proof.get("weights_present") is not False
        or proof.get("architecture_configs_present") is not False
    ):
        raise RuntimeError("Existing private Dataset Vault audit failed; no replacement will be created.")
    return proof


def ensure_zero_gpu_gradio_space(api: HfApi, repo_id: str) -> dict:
    """Create or verify a public Gradio Space using only the free ZeroGPU flavor."""
    created = False
    try:
        info = api.repo_info(repo_id=repo_id, repo_type="space")
    except RepositoryNotFoundError:
        api.create_repo(
            repo_id=repo_id,
            repo_type="space",
            space_sdk="gradio",
            space_hardware=ZERO_GPU,
            private=False,
            exist_ok=False,
        )
        created = True
        info = api.repo_info(repo_id=repo_id, repo_type="space")

    if getattr(info, "sdk", None) != "gradio":
        raise RuntimeError(
            f"{repo_id} already exists with SDK {getattr(info, 'sdk', None)!r}; refusing to convert it."
        )
    if getattr(info, "private", True) is not False:
        raise RuntimeError(f"{repo_id} is not public; refusing to silently change its access policy.")

    runtime = api.get_space_runtime(repo_id=repo_id)
    actual = str(getattr(runtime, "hardware", "") or "")
    requested = str(getattr(runtime, "requested_hardware", "") or "")
    if ZERO_GPU not in {actual, requested}:
        api.request_space_hardware(repo_id=repo_id, hardware=ZERO_GPU)
        runtime = api.get_space_runtime(repo_id=repo_id)
        actual = str(getattr(runtime, "hardware", "") or "")
        requested = str(getattr(runtime, "requested_hardware", "") or "")
    if ZERO_GPU not in {actual, requested}:
        raise RuntimeError(
            f"{repo_id} did not confirm free ZeroGPU (hardware={actual!r}, requested={requested!r}). "
            "Deployment stopped; paid or CPU Basic fallback is forbidden."
        )

    api.add_space_variable(repo_id=repo_id, key="GRADIO_SSR_MODE", value="false")
    return {
        "repo": repo_id,
        "created": created,
        "sdk": "gradio",
        "visibility": "public",
        "hardware": ZERO_GPU,
        "ssr_disabled": True,
    }


def set_space_secrets(api: HfApi, repo_id: str, secrets_map: dict[str, str]) -> None:
    for key, value in secrets_map.items():
        if value:
            api.add_space_secret(repo_id=repo_id, key=key, value=value)


def request_json(url: str, *, body: dict | None = None, headers: dict | None = None, timeout: int = 30):
    data = None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "VORAX-Free-ZeroGPU-Deploy/1.0",
    }
    if headers:
        request_headers.update(headers)
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=data,
        headers=request_headers,
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def wait_health(url: str, timeout_seconds: int = 600) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not checked"
    while time.monotonic() < deadline:
        try:
            status, payload = request_json(url, timeout=15)
            if status == 200 and payload.get("status") == "ok":
                return payload
            last_error = f"HTTP {status}: health payload not ready"
        except Exception as exc:
            last_error = type(exc).__name__
        time.sleep(8)
    raise RuntimeError(f"Health check did not pass for {url}: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deploy VORAX Teacher + Runtime to two free Gradio/ZeroGPU Spaces."
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Create/verify two ZeroGPU Spaces, set private secrets, upload code and run no-inference smoke tests.",
    )
    args = parser.parse_args()
    if not args.deploy:
        parser.error("Pass --deploy to authorize live Space creation and upload.")

    commit_sha, release_proof = local_release_gate()
    values = prompt_secrets()

    api = HfApi(token=values["HF_SPACE_TOKEN"])
    identity = api.whoami()
    username = str(identity.get("name") or identity.get("fullname") or "")
    if username.casefold() != OWNER.casefold():
        raise RuntimeError(
            f"HF_SPACE_TOKEN authenticated as {username!r}, not {OWNER!r}; refusing deployment."
        )

    vault_proof = inspect_existing_vault(values["HF_VAULT_TOKEN"])

    with tempfile.TemporaryDirectory(prefix="vorax-hf-zerogpu-") as temporary:
        base = Path(temporary)
        teacher_dir = base / "teacher"
        runtime_dir = base / "runtime"
        teacher_dir.mkdir()
        runtime_dir.mkdir()
        build_teacher_package(teacher_dir)
        build_runtime_package(runtime_dir)

        # Create/verify both Spaces before uploading either service package.
        teacher_space = ensure_zero_gpu_gradio_space(api, TEACHER_REPO)
        runtime_space = ensure_zero_gpu_gradio_space(api, RUNTIME_REPO)

        teacher_secrets = {
            "HF_TOKEN": values["HF_TOKEN"],
            "HF_READ_TOKEN": values["HF_READ_TOKEN"],
            "VORAX_CHILD_ID": CHILD_ID,
            "VORAX_KAGGLE_CHILD_API_KEY": values["VORAX_KAGGLE_CHILD_API_KEY"],
            "VORAX_TEACHER_ATTESTATION_KEY": values["VORAX_TEACHER_ATTESTATION_KEY"],
            "VORAX_ALLOW_BILLABLE_INFERENCE": "false",
        }
        runtime_secrets = {
            "VORAX_CHILD_ID": CHILD_ID,
            "VORAX_CHILD_ROLE": "hf-runtime",
            "VORAX_KAGGLE_CHILD_API_KEY": values["VORAX_KAGGLE_CHILD_API_KEY"],
            "VORAX_HOME_REPO": HOME_REPO,
            "VORAX_HOME_BRANCH": "main",
            "VORAX_HOME_READ_TOKEN": values["VORAX_HOME_READ_TOKEN"],
            "VORAX_HOME_WRITE_TOKEN": values["VORAX_HOME_WRITE_TOKEN"],
            "HF_VAULT_TOKEN": values["HF_VAULT_TOKEN"],
            "HF_VAULT_REPO": VAULT_REPO,
            "HF_INFERENCE_TOKEN": values["HF_TOKEN"],
            "VORAX_TEACHER_ATTESTATION_KEY": values["VORAX_TEACHER_ATTESTATION_KEY"],
            "VORAX_TEACHER_URL": TEACHER_URL,
            "VORAX_ALLOW_BILLABLE_INFERENCE": "false",
        }
        teacher_model = os.getenv("VORAX_TEACHER_MODEL", "").strip()
        if teacher_model:
            runtime_secrets["VORAX_TEACHER_MODEL"] = teacher_model

        set_space_secrets(api, TEACHER_REPO, teacher_secrets)
        set_space_secrets(api, RUNTIME_REPO, runtime_secrets)

        api.upload_folder(
            repo_id=TEACHER_REPO,
            repo_type="space",
            folder_path=str(teacher_dir),
            path_in_repo="",
            commit_message=f"Deploy VORAX Teacher Gradio/ZeroGPU from verified release {release_proof['release_tag']}",
        )
        api.upload_folder(
            repo_id=RUNTIME_REPO,
            repo_type="space",
            folder_path=str(runtime_dir),
            path_in_repo="",
            commit_message=f"Deploy VORAX Runtime Gradio/ZeroGPU from verified release {release_proof['release_tag']}",
        )

    teacher_health = wait_health(TEACHER_URL + "/health")
    if teacher_health.get("metadata_token_configured") is not True:
        raise RuntimeError("Teacher metadata read credentials are missing.")
    if teacher_health.get("attestation_configured") is not True:
        raise RuntimeError("Teacher attestation key is missing.")
    if teacher_health.get("free_provider_only") is not True:
        raise RuntimeError("Teacher billable inference is not safely disabled.")

    status, discovery = request_json(
        TEACHER_URL + "/teacher/discover?query=language&limit=3&source=models",
        timeout=60,
    )
    if status != 200 or discovery.get("status") != "metadata_only":
        raise RuntimeError("Teacher metadata-only discovery smoke test failed.")
    if discovery.get("weights_downloaded") is not False:
        raise RuntimeError("Teacher discovery did not prove the weight-free policy.")

    runtime_health = wait_health(RUNTIME_URL + "/health")
    required = runtime_health.get("required_secrets", {})
    if runtime_health.get("ready") is not True or not all(required.values()):
        raise RuntimeError("Runtime is live but not ready; inspect credentials, Home index and Vault health.")
    if runtime_health.get("home_method_bodies_loaded") is not False:
        raise RuntimeError("Runtime Home metadata boundary failed.")

    status, runtime_smoke = request_json(
        RUNTIME_URL + "/vorax/x",
        body={
            "x": "hello, who are you",
            "child_id": CHILD_ID,
            "api_key": values["VORAX_KAGGLE_CHILD_API_KEY"],
        },
        timeout=60,
    )
    if status != 200 or runtime_smoke.get("status") != "verified":
        raise RuntimeError("Authenticated Runtime Input X identity smoke test failed.")
    if runtime_smoke.get("method") != "runtime.identity":
        raise RuntimeError("Runtime smoke test resolved an unexpected method.")

    print(json.dumps({
        "status": "teacher_and_runtime_deployed",
        "release_gate": release_proof,
        "home_commit": commit_sha,
        "github_actions_verified": True,
        "account": OWNER,
        "teacher": {**teacher_space, "url": TEACHER_URL, "health": "passed", "metadata_discovery": "passed"},
        "runtime": {**runtime_space, "url": RUNTIME_URL, "health": "ready", "input_x": "verified"},
        "vault_read_only_predeploy_audit": vault_proof,
        "billable_inference_enabled": False,
        "remote_inference_smoke_performed": False,
        "kaggle_gpu_used": False,
        "model_weights_hosted": False,
        "knowledge_approval_authority": "GitHub Home merge",
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HfHubHTTPError, urllib.error.HTTPError) as exc:
        print(
            f"Hugging Face deployment failed: {type(exc).__name__}. "
            "No secret values were printed; no CPU Basic or paid-hardware fallback was attempted.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    except Exception as exc:
        print(f"VORAX ZeroGPU deployment stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
