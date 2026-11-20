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




def pending_duplicate_reason(record: dict) -> str | None:
    """Use only pending-index metadata to prevent two Children proposing the same method."""
    api, repo = _client()
    pending = _read_json(api, repo, "vlang/pending/index.json", {"version": "vlang-index/1", "records": []})
    records = pending.get("records", [])
    if not isinstance(records, list):
        raise VaultError("Vault pending index has an invalid records field")
    for item in records:
        if not isinstance(item, dict):
            continue
        if item.get("id") == record.get("id"):
            return "method_id_already_pending"
        item_domain = str(item.get("domain", "")).casefold()
        record_domain = str(record.get("domain", "")).casefold()
        if item_domain == record_domain and str(item.get("task", "")).casefold() == str(record.get("task", "")).casefold():
            return "method_task_already_pending"
        item_triggers = {str(value).casefold() for value in item.get("triggers", [])}
        record_triggers = {str(value).casefold() for value in record.get("triggers", [])}
        if item_domain == record_domain and item_triggers.intersection(record_triggers):
            return "method_trigger_already_pending"
    return None



def write_verified_artifact_to_vault(artifact_path: str | Path, *, child_id: str, x_value: str, provider: str) -> dict:
    """Independently validate and persist a real Runtime-produced remote artifact."""
    from .vlang import CHILD_RE

    if not isinstance(child_id, str) or not CHILD_RE.fullmatch(child_id):
        raise VaultError("Invalid Child ID for artifact provenance")
    if not isinstance(provider, str) or not provider.strip():
        raise VaultError("Remote provider identity is required for artifact provenance")

    root = Path(os.getenv("LEGEND_ARTIFACT_DIR", "data/artifacts")).resolve()
    path = Path(artifact_path).resolve()
    if root not in path.parents:
        raise VaultError("Artifact path is outside Runtime's configured artifact directory")
    proof = _verify(path)
    raw = path.read_bytes()
    sha = proof["sha256"]
    target = f"artifacts/{proof['folder']}/{sha}.{proof['kind']}"
    report_path = f"reports/verification/artifacts/{sha}.json"
    api, repo = _client()

    existing_files = set(api.list_repo_files(repo_id=repo, repo_type="dataset"))
    if target in existing_files:
        existing = _read_file(api, repo, target)
        if hashlib.sha256(existing).hexdigest() != sha:
            raise VaultError("Existing Vault artifact path has a checksum conflict")
    else:
        _upload(api, repo, target, raw, "Runtime-verified remote VORAX artifact")

    report = {
        "version": "vorax-artifact-verification/v1",
        "repo_type": "dataset",
        "path": target,
        "kind": proof["kind"],
        "sha256": sha,
        "size_bytes": proof["size_bytes"],
        "child_id": child_id,
        "x_summary": str(x_value)[:240],
        "provider": provider,
        "execution": "remote_provider",
        "kaggle_t4_used": False,
        "weights_downloaded": False,
        "local_model_loaded": False,
        "verification": proof,
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    report_raw = json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8")
    _upload(api, repo, report_path, report_raw, "Record independent Runtime verification for real remote artifact")

    checksum_doc = _read_json(api, repo, "manifest/checksums.json", {"version": "sha256/v1", "files": {}})
    checksums = checksum_doc.get("files", {})
    if not isinstance(checksums, dict):
        raise VaultError("Vault checksum manifest has an invalid files field")
    checksums[target] = sha
    checksums[report_path] = hashlib.sha256(report_raw).hexdigest()
    checksum_raw = json.dumps(checksum_doc, indent=2, sort_keys=True).encode("utf-8")
    _upload(api, repo, "manifest/checksums.json", checksum_raw, "Update checksums for verified remote artifact")
    return {
        "status": "stored_verified",
        "repo": repo,
        "repo_type": "dataset",
        "path": target,
        "verification_report": report_path,
        "sha256": sha,
        "size_bytes": proof["size_bytes"],
        "kind": proof["kind"],
        "provider": provider,
        "kaggle_t4_used": False,
    }


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
    if not current:
        for item in records:
            if not isinstance(item, dict):
                continue
            item_domain = str(item.get("domain", "")).casefold()
            record_domain = record["domain"].casefold()
            if item_domain == record_domain and str(item.get("task", "")).casefold() == record["task"].casefold():
                raise VaultError("An equivalent V-Lang task is already pending in the Vault")
            item_triggers = {str(value).casefold() for value in item.get("triggers", [])}
            record_triggers = {str(value).casefold() for value in record["triggers"]}
            if item_domain == record_domain and item_triggers.intersection(record_triggers):
                raise VaultError("An equivalent V-Lang trigger is already pending in the Vault")
    entry = {
        "id": method_id,
        "domain": record["domain"],
        "task": record["task"],
        "triggers": list(record["triggers"]),
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

    home_by_id = {item["id"]: item for item in trusted_index["records"]}
    promoted = []
    promoted_methods = []
    for entry in pending_records:
        if not isinstance(entry, dict) or entry.get("id") not in home_by_id:
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
        candidate = records[0]
        home_summary = home_by_id[method_id]
        if (
            candidate["domain"] != home_summary["domain"]
            or candidate["task"] != home_summary["task"]
            or candidate["triggers"] != home_summary["triggers"]
        ):
            raise VaultError(
                f"Pending method {method_id} does not match Home's merged metadata summary"
            )
        canonical = dump_record(candidate).encode("utf-8")
        canonical_sha = hashlib.sha256(canonical).hexdigest()
        if home_summary.get("method_sha256") != canonical_sha:
            raise VaultError(
                f"Pending method {method_id} checksum does not match Home's merged method metadata"
            )
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
        promoted_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        verified_entry = {
            "id": method_id,
            "path": target,
            "sha256": canonical_sha,
            "size_bytes": len(canonical),
            "home_certified": True,
            "home_pr": entry.get("home_pr"),
            "promoted_at": promoted_at,
        }
        existing_entry = next((x for x in verified_records if isinstance(x, dict) and x.get("id") == method_id), None)
        if existing_entry:
            existing_entry.update(verified_entry)
        else:
            verified_records.append(verified_entry)
        entry["status"] = "merged_home_and_promoted"
        entry["home_certification"] = "confirmed_by_home_index"
        entry["promoted_at"] = promoted_at
        promoted_methods.append(candidate)

        report_path = entry.get("verification_report")
        if report_path:
            report = _read_json(api, repo, report_path, {})
            report["home_certification"] = "confirmed_by_home_index"
            report["home_index_version"] = trusted_index["version"]
            report["home_index_summary"] = {
                "id": home_summary["id"],
                "domain": home_summary["domain"],
                "task": home_summary["task"],
                "triggers": home_summary["triggers"],
                "method_sha256": home_summary["method_sha256"],
            }
            report["promoted_at"] = promoted_at
            report_raw = json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8")
            _upload(api, repo, report_path, report_raw, "Record Home confirmation of verified V-Lang method")
            checksums[report_path] = hashlib.sha256(report_raw).hexdigest()
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
        "promoted_methods": promoted_methods,
        "home_index_methods": len(home_by_id),
        "repo_type": "dataset",
        "vault_repo": repo,
    }
