"""Mirror trusted GitHub Home methods and independently verified artifacts to HF Dataset Vault."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import time
import wave
from pathlib import Path

MAX_BYTES = 40 * 1024 * 1024
DEFAULT_REPO = "Musombi/revelacode-vorax-vault"


class VaultError(RuntimeError):
    """Vault credentials, quota, or artifact verification failure."""


def _client():
    """Open the existing private Dataset only. Never create, convert, or replace a Vault."""
    token = os.getenv("HF_VAULT_TOKEN", "").strip()
    if not token:
        raise VaultError("HF_VAULT_TOKEN is not configured")
    from huggingface_hub import HfApi

    repo = os.getenv("HF_VAULT_REPO", DEFAULT_REPO)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise VaultError("Invalid Vault repository ID")
    api = HfApi(token=token)
    try:
        info = api.repo_info(repo_id=repo, repo_type="dataset")
    except Exception as exc:
        raise VaultError("Existing private Dataset Vault could not be opened; refusing to create a replacement") from exc
    if not getattr(info, "private", False):
        raise VaultError(f"Vault dataset {repo} is public; refusing to upload methods or artifacts")
    _assert_no_model_payloads(api, repo)
    return api, repo


def _assert_no_model_payloads(api, repo: str) -> list[str]:
    """Fail closed if the Dataset contains model weights or model architecture configs."""
    try:
        files = list(api.list_repo_files(repo_id=repo, repo_type="dataset"))
    except Exception as exc:
        raise VaultError("Could not inspect the existing Vault file list") from exc
    weight_suffixes = (".safetensors", ".pt", ".pth", ".ckpt", ".gguf", ".onnx", ".model", ".bin", ".h5", ".pb", ".tflite", ".msgpack", ".weights")
    for name in files:
        lowered = name.lower()
        if "hf-model/" in lowered or lowered == "hf-model" or lowered.endswith(weight_suffixes) or lowered.endswith("/pytorch_model.bin"):
            raise VaultError(f"Forbidden model payload found in Vault: {name}")
        if lowered.endswith("/config.json") or lowered == "config.json":
            try:
                raw = _read_file(api, repo, name)
                config = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                raise VaultError(f"Could not inspect config file in Vault: {name}") from exc
            if isinstance(config, dict) and ({"architectures", "model_type"} & set(config)):
                raise VaultError(f"Model architecture config is forbidden in Vault: {name}")
    return files


def _read_file(api, repo: str, filename: str) -> bytes:
    from huggingface_hub import hf_hub_download
    try:
        cached = hf_hub_download(
            repo_id=repo,
            filename=filename,
            repo_type="dataset",
            token=os.getenv("HF_VAULT_TOKEN", "").strip(),
        )
        return Path(cached).read_bytes()
    except Exception as exc:
        raise VaultError(f"Could not read existing Vault file {filename}: {type(exc).__name__}") from exc


def _read_json(api, repo: str, filename: str, default: dict) -> dict:
    try:
        value = json.loads(_read_file(api, repo, filename).decode("utf-8"))
    except VaultError as exc:
        # Missing index files may be initialized. Other read failures must stop the write.
        if "EntryNotFound" in str(exc) or "404" in str(exc):
            return json.loads(json.dumps(default))
        raise
    if not isinstance(value, dict):
        raise VaultError(f"Vault JSON file is not an object: {filename}")
    return value


def _write_json(api, repo: str, filename: str, value: dict, message: str) -> bytes:
    raw = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    _upload(api, repo, filename, raw, message)
    return raw


def inspect_vault() -> dict:
    """Read-only proof of the existing Vault's repo type, privacy, layout, and weight-free state."""
    api, repo = _client()
    files = set(api.list_repo_files(repo_id=repo, repo_type="dataset"))
    required = [
        "vlang/index.json",
        "vlang/pending/index.json",
        "manifest/checksums.json",
        "manifest/runtime-version.json",
        "manifest/sync-status.json",
        "reports/devour/latest.json",
        "reports/verification/latest.json",
        "reports/go-no-go/latest.json",
    ]
    missing = [name for name in required if name not in files]
    artifact_folders = {
        name: any(path.startswith(f"artifacts/{name}/") for path in files)
        for name in ("images", "audio", "video")
    }
    return {
        "status": "verified" if not missing else "incomplete",
        "repo": repo,
        "repo_type": "dataset",
        "private": True,
        "files_total": len(files),
        "required_files": required,
        "missing_files": missing,
        "artifact_folders": artifact_folders,
        "weights_present": False,
        "architecture_configs_present": False,
    }

def _verify(path: Path) -> dict:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise VaultError("Missing, empty, or oversized artifact")
    suffix = path.suffix.lower()

    if suffix == ".png":
        from PIL import Image, ImageStat

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
            if image.width < 16 or image.height < 16:
                raise VaultError("PNG dimensions are too small")
            extrema = ImageStat.Stat(image.convert("RGB")).extrema
            if all(low == high for low, high in extrema):
                raise VaultError("PNG appears to be a single-colour blank")
            detail = {"width": image.width, "height": image.height}
        kind, folder = "png", "images"

    elif suffix == ".wav":
        with wave.open(str(path), "rb") as audio:
            frames, rate, channels = audio.getnframes(), audio.getframerate(), audio.getnchannels()
            audio.rewind()
            sample = audio.readframes(min(frames, max(rate, 1) * 10))
            if frames <= 0 or rate <= 0 or channels <= 0 or not sample or not any(sample):
                raise VaultError("WAV is silent or invalid")
            detail = {
                "duration_seconds": round(frames / rate, 3),
                "sample_rate": rate,
                "channels": channels,
            }
        kind, folder = "wav", "audio"

    elif suffix == ".mp4":
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                "-show_entries", "stream=nb_read_frames,width,height,duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=25, check=True,
        )
        streams = json.loads(result.stdout).get("streams", [])
        if not streams:
            raise VaultError("MP4 has no video stream")
        frames = int(streams[0].get("nb_read_frames") or 0)
        duration = float(streams[0].get("duration") or 0)
        if frames <= 10 or duration <= 0:
            raise VaultError("MP4 must contain more than 10 decoded frames and positive duration")
        detail = {
            "frame_count": frames,
            "width": streams[0].get("width"),
            "height": streams[0].get("height"),
            "duration_seconds": duration,
        }
        kind, folder = "mp4", "video"

    else:
        raise VaultError("Only PNG, WAV, and MP4 artifacts are accepted")

    return {
        "kind": kind,
        "folder": folder,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
        **detail,
    }


def _upload(api, repo: str, path: str, data: bytes, commit_message: str) -> None:
    api.upload_file(
        path_or_fileobj=io.BytesIO(data),
        path_in_repo=path,
        repo_id=repo,
        repo_type="dataset",
        commit_message=commit_message,
    )



def write_pending_method(record: dict, *, child_id: str, verification: dict) -> dict:
    """Runtime-only write of a method that passed the trusted V-Lang interpreter."""
    from .vlang import CHILD_RE, dump_record, validate_template

    try:
        validate_template(record, run_tests=True)
    except Exception as exc:
        raise VaultError("Runtime method verification failed; pending Vault write refused") from exc
    if not isinstance(child_id, str) or not CHILD_RE.fullmatch(child_id):
        raise VaultError("Invalid Child ID for Vault provenance")
    if not isinstance(verification, dict) or verification.get("status") != "runtime_tests_passed":
        raise VaultError("A trusted Runtime test report is required before a pending Vault write")

    api, repo = _client()
    raw = dump_record(record).encode("utf-8")
    method_sha = hashlib.sha256(raw).hexdigest()
    if verification.get("method_sha256") != method_sha:
        raise VaultError("Runtime verification checksum does not match the canonical V-Lang method")
    method_id = record["id"]
    target = f"vlang/pending/{method_id}.vlang"
    report_path = f"reports/verification/pending/{method_id}.json"
    existing_files = set(api.list_repo_files(repo_id=repo, repo_type="dataset"))
    if target in existing_files:
        existing = _read_file(api, repo, target)
        if hashlib.sha256(existing).hexdigest() != method_sha:
            raise VaultError("A different pending method already uses this ID; refusing to overwrite it")

    pending = _read_json(api, repo, "vlang/pending/index.json", {
        "version": "vlang-index/1",
        "records": [],
        "note": "Runtime-verified proposals remain pending until GitHub Home review and merge.",
    })
    records = pending.get("records", [])
    if not isinstance(records, list):
        raise VaultError("Vault pending index has an invalid records field")
    current = next((item for item in records if isinstance(item, dict) and item.get("id") == method_id), None)
    if current and current.get("sha256") != method_sha:
        raise VaultError("Pending index contains a conflicting method checksum")
    entry = {
        "id": method_id,
        "path": target,
        "sha256": method_sha,
        "size_bytes": len(raw),
        "child_id": child_id,
        "status": "runtime_verified_pending_home_review",
        "home_certification": "pending",
        "verification_report": report_path,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if current:
        current.update(entry)
    else:
        records.append(entry)
    pending["records"] = records

    report = {
        "version": "vorax-verification-report/v1",
        "repo_type": "dataset",
        "method_id": method_id,
        "method_path": target,
        "method_sha256": method_sha,
        "child_id": child_id,
        "verification": verification,
        "home_certification": "pending",
        "home_pr": None,
        "weights_downloaded": False,
        "local_model_loaded": False,
        "created_at": entry["created_at"],
    }
    checksum_doc = _read_json(api, repo, "manifest/checksums.json", {"version": "sha256/v1", "files": {}})
    checksums = checksum_doc.get("files", {})
    if not isinstance(checksums, dict):
        raise VaultError("Vault checksum manifest has an invalid files field")

    _upload(api, repo, target, raw, "Runtime-verified V-Lang method awaiting Home review")
    report_raw = json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8")
    _upload(api, repo, report_path, report_raw, "Record Runtime verification evidence for pending V-Lang method")
    checksums[target] = method_sha
    checksums[report_path] = hashlib.sha256(report_raw).hexdigest()
    pending_raw = json.dumps(pending, indent=2, ensure_ascii=False).encode("utf-8")
    checksums["vlang/pending/index.json"] = hashlib.sha256(pending_raw).hexdigest()
    _upload(api, repo, "vlang/pending/index.json", pending_raw, "Update VORAX pending V-Lang index")
    checksum_raw = json.dumps(checksum_doc, indent=2, sort_keys=True).encode("utf-8")
    _upload(api, repo, "manifest/checksums.json", checksum_raw, "Update VORAX Vault SHA-256 checksums")
    return {
        "status": "written_pending",
        "repo": repo,
        "repo_type": "dataset",
        "path": target,
        "verification_report": report_path,
        "method_sha256": method_sha,
        "home_certification": "pending",
    }


def mark_pending_home_submission(method_id: str, pull_request: str) -> dict:
    """Attach a Home PR URL to the pending Vault record without promoting it to verified."""
    api, repo = _client()
    pending = _read_json(api, repo, "vlang/pending/index.json", {"version": "vlang-index/1", "records": []})
    records = pending.get("records", [])
    entry = next((item for item in records if isinstance(item, dict) and item.get("id") == method_id), None)
    if entry is None:
        raise VaultError("Pending method was not found in the Vault index")
    entry["home_pr"] = pull_request
    entry["status"] = "submitted_for_home_review"
    report_path = entry.get("verification_report")
    report = _read_json(api, repo, report_path, {}) if report_path else {}
    report["home_pr"] = pull_request
    report["home_certification"] = "pending"
    report_raw = json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8")
    pending_raw = json.dumps(pending, indent=2, ensure_ascii=False).encode("utf-8")
    checksum_doc = _read_json(api, repo, "manifest/checksums.json", {"version": "sha256/v1", "files": {}})
    checksums = checksum_doc.setdefault("files", {})
    checksums[report_path] = hashlib.sha256(report_raw).hexdigest()
    checksums["vlang/pending/index.json"] = hashlib.sha256(pending_raw).hexdigest()
    _upload(api, repo, report_path, report_raw, "Attach Home review link to VORAX verification report")
    _upload(api, repo, "vlang/pending/index.json", pending_raw, "Record VORAX pending method Home review state")
    _upload(api, repo, "manifest/checksums.json", json.dumps(checksum_doc, indent=2, sort_keys=True).encode("utf-8"),
            "Update VORAX Vault SHA-256 checksums")
    return {"status": "submitted_for_home_review", "home_pr": pull_request}


def promote_pending_home_methods(home_index: dict) -> dict:
    """Promote pending methods only when Home's metadata index confirms the merged method ID.

    The Runtime reads the V-Lang candidate from the Vault itself. It never fetches a
    method body from GitHub Home. Home contributes only its metadata-only index.
    """
    from .home_index import parse_method_index
    from .vlang import dump_record, parse_document

    try:
        trusted_index = parse_method_index(json.dumps(home_index))
    except Exception as exc:
        raise VaultError("Home metadata index failed validation; no pending method was promoted") from exc

    api, repo = _client()
    pending = _read_json(api, repo, "vlang/pending/index.json", {"version": "vlang-index/1", "records": []})
    verified = _read_json(api, repo, "vlang/index.json", {"version": "vlang-index/1", "records": []})
    checksums_doc = _read_json(api, repo, "manifest/checksums.json", {"version": "sha256/v1", "files": {}})
    pending_records = pending.get("records", [])
    verified_records = verified.get("records", [])
    checksums = checksums_doc.get("files", {})
    if not all(isinstance(x, list) for x in (pending_records, verified_records)) or not isinstance(checksums, dict):
        raise VaultError("Vault indexes or checksum manifest are malformed")

    home_ids = {item["id"] for item in trusted_index["records"]}
    promoted = []
    for entry in pending_records:
        if not isinstance(entry, dict) or entry.get("id") not in home_ids:
            continue
        method_id = entry["id"]
        pending_path = entry.get("path") or f"vlang/pending/{method_id}.vlang"
        raw = _read_file(api, repo, pending_path)
        try:
            records = parse_document(raw.decode("utf-8"))
        except Exception as exc:
            raise VaultError(f"Pending method {method_id} failed trusted V-Lang revalidation") from exc
        if len(records) != 1 or records[0]["id"] != method_id:
            raise VaultError(f"Pending Vault path does not contain the indexed method {method_id}")
        canonical = dump_record(records[0]).encode("utf-8")
        canonical_sha = hashlib.sha256(canonical).hexdigest()
        if hashlib.sha256(raw).hexdigest() != canonical_sha or entry.get("sha256") != canonical_sha:
            raise VaultError(f"Pending method checksum changed for {method_id}")
        target = f"vlang/verified/{method_id}.vlang"
        if target in set(api.list_repo_files(repo_id=repo, repo_type="dataset")):
            existing = _read_file(api, repo, target)
            if hashlib.sha256(existing).hexdigest() != canonical_sha:
                raise VaultError(f"Verified Vault method ID conflict for {method_id}")
        else:
            _upload(api, repo, target, canonical, "Promote Home-merged V-Lang method from Vault pending")
        checksums[target] = canonical_sha
        verified_entry = {
            "id": method_id,
            "path": target,
            "sha256": canonical_sha,
            "size_bytes": len(canonical),
            "home_certified": True,
            "home_pr": entry.get("home_pr"),
        }
        existing_entry = next((x for x in verified_records if isinstance(x, dict) and x.get("id") == method_id), None)
        if existing_entry:
            existing_entry.update(verified_entry)
        else:
            verified_records.append(verified_entry)
        entry["status"] = "merged_home_and_promoted"
        entry["home_certification"] = "confirmed_by_home_index"
        promoted.append(method_id)

    if promoted:
        verified_records.sort(key=lambda item: item.get("id", ""))
        verified["records"] = verified_records
        pending["records"] = pending_records
        verified_raw = json.dumps(verified, indent=2, ensure_ascii=False).encode("utf-8")
        pending_raw = json.dumps(pending, indent=2, ensure_ascii=False).encode("utf-8")
        checksums["vlang/index.json"] = hashlib.sha256(verified_raw).hexdigest()
        checksums["vlang/pending/index.json"] = hashlib.sha256(pending_raw).hexdigest()
        _upload(api, repo, "vlang/index.json", verified_raw, "Update Home-confirmed verified V-Lang index")
        _upload(api, repo, "vlang/pending/index.json", pending_raw, "Update Home-confirmed pending V-Lang index")
        checksum_raw = json.dumps(checksums_doc, indent=2, sort_keys=True).encode("utf-8")
        _upload(api, repo, "manifest/checksums.json", checksum_raw, "Update checksums after Home method promotion")
    return {
        "status": "promoted" if promoted else "no_home_merged_pending_methods",
        "promoted_method_ids": promoted,
        "home_index_methods": len(home_ids),
        "repo_type": "dataset",
        "vault_repo": repo,
    }


def mirror_home_and_artifacts(records, artifact_root):
    api, repo = _client()
    from .vlang import dump_record

    synced_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    checksum_doc = _read_json(api, repo, "manifest/checksums.json", {"version": "sha256/v1", "files": {}})
    checksums = dict(checksum_doc.get("files", {}))
    if not isinstance(checksums, dict):
        raise VaultError("Vault checksum manifest has an invalid files field")
    methods = []
    artifact_proofs = []
    bytes_uploaded = 0

    for record in sorted(records, key=lambda item: str(item.get("id", ""))):
        method_id = str(record.get("id", ""))
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", method_id):
            continue
        raw = dump_record(record).encode("utf-8")
        target = f"vlang/verified/{method_id}.vlang"
        _upload(api, repo, target, raw, "Mirror Home-merged verified V-Lang method")
        checksums[target] = hashlib.sha256(raw).hexdigest()
        bytes_uploaded += len(raw)
        methods.append({"id": method_id, "path": target, "sha256": checksums[target], "size_bytes": len(raw)})

    root = Path(artifact_root).resolve()
    if root.exists():
        for path in sorted(root.iterdir()):
            if path.suffix.lower() not in {".png", ".wav", ".mp4"}:
                continue
            try:
                resolved = path.resolve()
                if root not in resolved.parents:
                    continue
                proof = _verify(resolved)
            except Exception:
                continue
            target = f"artifacts/{proof['folder']}/{proof['sha256']}.{proof['kind']}"
            with resolved.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if not raw or len(raw) > MAX_BYTES:
                continue
            _upload(api, repo, target, raw, "Mirror independently verified VORAX artifact")
            checksums[target] = proof["sha256"]
            bytes_uploaded += len(raw)
            proof["path"] = target
            artifact_proofs.append(proof)

    pending_index = _read_json(api, repo, "vlang/pending/index.json", {
        "version": "vlang-index/1",
        "records": [],
        "note": "Runtime-verified proposals remain pending until GitHub Home review and merge.",
    })
    if not isinstance(pending_index.get("records"), list):
        raise VaultError("Vault pending index has an invalid records field")
    verified_index = {"version": "vlang-index/1", "records": methods, "synced_at": synced_at}
    verified_index_raw = json.dumps(verified_index, indent=2, ensure_ascii=False).encode("utf-8")
    pending_index_raw = json.dumps(pending_index, indent=2, ensure_ascii=False).encode("utf-8")
    checksums["vlang/index.json"] = hashlib.sha256(verified_index_raw).hexdigest()
    checksums["vlang/pending/index.json"] = hashlib.sha256(pending_index_raw).hexdigest()
    manifest = {
        "version": "vorax-vault-manifest/v1",
        "vault_repo": repo,
        "repo_type": "dataset",
        "synced_at": synced_at,
        "methods_mirrored": len(methods),
        "artifacts_verified_and_mirrored": len(artifact_proofs),
        "bytes_uploaded": bytes_uploaded,
        "artifact_proofs": artifact_proofs,
    }
    files = {
        "vlang/index.json": verified_index_raw,
        "vlang/pending/index.json": pending_index_raw,
        "manifest/checksums.json": b"",
        "manifest/runtime-version.json": json.dumps({
            "engine": "VORAX",
            "package_version": os.getenv("VORAX_RUNTIME_VERSION", "0.2.0"),
            "vlang_version": "vlang/1",
            "synced_at": synced_at,
        }, indent=2).encode("utf-8"),
        "manifest/sync-status.json": json.dumps({
            "status": "mirrored",
            "synced_at": synced_at,
            "home_methods": len(methods),
            "verified_artifacts": len(artifact_proofs),
            "vault_capacity_verified": False,
        }, indent=2).encode("utf-8"),
        "reports/verification/latest.json": json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
        "reports/devour/latest.json": json.dumps({
            "status": "metadata_discovery_only",
            "synced_at": synced_at,
            "methods_mirrored": len(methods),
            "note": "A method is not counted as learned from discovery metadata alone.",
        }, indent=2).encode("utf-8"),
        "reports/go-no-go/latest.json": json.dumps({
            "status": "BLOCKED",
            "checked_at": synced_at,
            "evidence": {
                "home_methods_mirrored": len(methods),
                "verified_media_artifacts_mirrored": len(artifact_proofs),
                "vault_capacity_verified": False,
                "live_child_health_campaign_gpu_and_coverage": "not_certified_by_vault_sync",
            },
            "reason": "Only Home can authorize GO. Live deployment, quota, real modality output, campaign, and coverage evidence remain to be verified.",
        }, indent=2).encode("utf-8"),
    }
    for target, raw in files.items():
        if target != "manifest/checksums.json":
            checksums[target] = hashlib.sha256(raw).hexdigest()
    files["manifest/checksums.json"] = json.dumps(
        {"version": "sha256/v1", "files": checksums}, indent=2, sort_keys=True
    ).encode("utf-8")
    for target, raw in files.items():
        _upload(api, repo, target, raw, "Update VORAX Home-to-Vault index and manifests")
        bytes_uploaded += len(raw)

    manifest["manifest_files_written"] = sorted(files)
    manifest["bytes_uploaded"] = bytes_uploaded
    return manifest
