#!/usr/bin/env python3
"""Create the Kaggle script-kernel manifest without starting a Kaggle session."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "kaggle" / "child"
username = os.getenv("KAGGLE_USERNAME", "").strip()
slug = os.getenv("KAGGLE_KERNEL_SLUG", "revelacode-vorax-child").strip()
if not username:
    raise SystemExit("KAGGLE_USERNAME is required to prepare the kernel metadata")
if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", slug):
    raise SystemExit("KAGGLE_KERNEL_SLUG must be 1–50 letters, numbers, _ or -")

metadata = {
    "id": f"{username}/{slug}",
    "title": "RevelaCode VORAX GPU Research Child",
    "code_file": "vorax_child.py",
    "language": "python",
    "kernel_type": "script",
    "is_private": True,
    "enable_gpu": True,
    "enable_internet": True,
    "dataset_sources": [],
    "competition_sources": [],
    "kernel_sources": [],
    "model_sources": [],
}
release_ref = os.getenv("VORAX_RELEASE_REF") or os.getenv("GITHUB_REF_NAME", "").strip()
release_sha = os.getenv("VORAX_RELEASE_SHA") or os.getenv("GITHUB_SHA", "").strip().lower()
if not re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", release_ref):
    raise SystemExit("VORAX_RELEASE_REF must identify a published release tag")
if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
    raise SystemExit("VORAX_RELEASE_SHA must be the 40-character tagged commit SHA")

(root / "vorax-release.json").write_text(
    json.dumps({"schema": "vorax-release/v1", "ref": release_ref, "sha": release_sha}, indent=2) + "\n",
    encoding="utf-8",
)
target = root / "kernel-metadata.json"
target.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
print(f"Prepared Kaggle kernel source for {username}/{slug} at {release_ref}@{release_sha}; GPU has not been started.")
