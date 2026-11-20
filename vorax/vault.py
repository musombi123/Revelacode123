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
    token = os.getenv("HF_VAULT_TOKEN", "").strip()
    if not token:
        raise VaultError("HF_VAULT_TOKEN is not configured")
    from huggingface_hub import HfApi

    repo = os.getenv("HF_VAULT_REPO", DEFAULT_REPO)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise VaultError("Invalid Vault repository ID")
    api = HfApi(token=token)
    api.create_repo(repo_id=repo, repo_type="dataset", private=True, exist_ok=True)
    info = api.repo_info(repo_id=repo, repo_type="dataset")
    if not getattr(info, "private", False):
        raise VaultError(
            f"Vault dataset {repo} is public; refusing to upload Home methods or artifacts"
        )
    return api, repo


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


def mirror_home_and_artifacts(records, artifact_root):
    api, repo = _client()
    from .vlang import dump_record

    synced_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    checksums = {}
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

    pending_index = {
        "version": "vlang-index/1",
        "records": [],
        "note": "Unmerged proposals remain in GitHub pull requests and are not promoted to the verified Vault.",
    }
    verified_index = {"version": "vlang-index/1", "records": methods, "synced_at": synced_at}
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
        "vlang/index.json": json.dumps(verified_index, indent=2, ensure_ascii=False).encode("utf-8"),
        "vlang/pending/index.json": json.dumps(pending_index, indent=2, ensure_ascii=False).encode("utf-8"),
        "manifest/checksums.json": json.dumps({"version": "sha256/v1", "files": checksums}, indent=2, sort_keys=True).encode("utf-8"),
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
        _upload(api, repo, target, raw, "Update VORAX Home-to-Vault index and manifests")
        bytes_uploaded += len(raw)

    manifest["manifest_files_written"] = sorted(files)
    manifest["bytes_uploaded"] = bytes_uploaded
    return manifest
