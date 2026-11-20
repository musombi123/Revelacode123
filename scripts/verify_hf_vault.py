#!/usr/bin/env python3
"""Read-only inspection of the existing private VORAX Dataset Vault."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    if not os.getenv("HF_VAULT_TOKEN", "").strip():
        print("HF_VAULT_TOKEN is not configured; no Vault operation was attempted.")
        return 2
    os.environ.setdefault("HF_VAULT_REPO", "Musombi/revelacode-vorax-vault")
    from vorax.vault import inspect_vault

    proof = inspect_vault()
    print(json.dumps(proof, indent=2, ensure_ascii=False))
    return 0 if proof.get("status") == "verified" and proof.get("private") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
