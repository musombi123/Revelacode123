from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .child import verify_signature
from .tlang import TLangVerifier


class HomeGate:
    def __init__(self, trusted_children: dict[str, str], blocked_children: set[str] | None = None):
        self.trusted_children = trusted_children
        self.blocked_children = blocked_children or set()
        self.verifier = TLangVerifier()

    def verify(self, proposal: dict[str, Any], known_templates: list[dict[str, Any]]) -> dict[str, Any]:
        envelope = proposal.get("submission", {})
        child_id = envelope.get("child_id") if isinstance(envelope, dict) else None
        if child_id in self.blocked_children:
            return {"passed": False, "child_id": child_id, "issues": ["child is blocked by Home trust registry"]}
        signature_ok, identity = verify_signature(proposal, self.trusted_children)
        if not signature_ok: return {"passed": False, "child_id": child_id, "issues": [identity]}
        report = self.verifier.verify(proposal, known_templates)
        return {"passed": report.passed, "child_id": identity, "issues": report.issues, "tests_passed": report.tests_passed, "tests_total": report.tests_total, "fingerprint": report.fingerprint}


def load_trusted_children() -> dict[str, str]:
    raw = os.getenv("LEGEND_CHILD_HMAC_KEYS", "")
    if not raw: raise RuntimeError("LEGEND_CHILD_HMAC_KEYS secret is required by the Home merge gate")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in parsed.items()):
        raise RuntimeError("LEGEND_CHILD_HMAC_KEYS must be a JSON object mapping child IDs to HMAC secrets")
    return parsed


def load_blocked_children(path: str | Path) -> set[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    blocked = data.get("blocked_children", [])
    return set(blocked) if isinstance(blocked, list) else set()
