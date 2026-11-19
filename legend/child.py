from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .tlang import Composer, TLangVerifier, canonical_json, template_fingerprint


class ChildError(RuntimeError):
    pass


def sign_template(template: dict[str, Any], child_id: str, secret: str) -> dict[str, Any]:
    if not child_id or not secret: raise ChildError("child id and HMAC secret are required")
    signed = json.loads(json.dumps(template))
    envelope = {"child_id": child_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "nonce": uuid.uuid4().hex, "signature": ""}
    signed["submission"] = envelope
    payload = json.loads(json.dumps(signed))
    payload["submission"].pop("signature")
    signed["submission"]["signature"] = hmac.new(secret.encode("utf-8"), canonical_json(payload), hashlib.sha256).hexdigest()
    return signed


def verify_signature(template: dict[str, Any], secrets_by_child: dict[str, str]) -> tuple[bool, str]:
    envelope = template.get("submission")
    if not isinstance(envelope, dict): return False, "missing submission envelope"
    child_id, signature = envelope.get("child_id"), envelope.get("signature")
    if not isinstance(child_id, str) or not isinstance(signature, str): return False, "invalid submission identity or signature"
    secret = secrets_by_child.get(child_id)
    if not secret: return False, "untrusted child id"
    payload = json.loads(json.dumps(template))
    payload["submission"].pop("signature", None)
    expected = hmac.new(secret.encode("utf-8"), canonical_json(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected): return False, "submission signature mismatch"
    return True, child_id


class ChildEngine:
    def __init__(self, child_id: str, workspace: str | Path):
        self.child_id = child_id
        self.root = Path(workspace)
        self.templates_dir = self.root / "templates"
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "child_state.json"
        self.verifier = TLangVerifier()

    def local_templates(self) -> list[dict[str, Any]]:
        items = []
        for path in sorted(self.templates_dir.glob("*.json")):
            try: items.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError): continue
        return items

    def _state(self) -> dict[str, Any]:
        try: return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): return {"consecutive_rejections": 0, "paused": False}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def ingest(self, candidate: dict[str, Any], known_templates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        state = self._state()
        if state.get("paused"): raise ChildError("child is paused after 100 consecutive rejected proposals")
        report = self.verifier.verify(candidate, (known_templates or []) + self.local_templates())
        if not report.passed:
            state["consecutive_rejections"] = int(state.get("consecutive_rejections", 0)) + 1
            if state["consecutive_rejections"] >= 100: state["paused"] = True
            self._save_state(state)
            raise ChildError("proposal rejected: " + "; ".join(report.issues))
        state["consecutive_rejections"] = 0
        state["paused"] = False
        self._save_state(state)
        path = self.templates_dir / (candidate["id"] + ".json")
        path.write_bytes(canonical_json(candidate))
        return {"status": "ACCEPTED_LOCALLY", "template_id": candidate["id"], "fingerprint": template_fingerprint(candidate)}

    def compose(self, template_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
        for template in self.local_templates():
            if template.get("id") == template_id: return Composer().run(template, inputs)
        return {"status": "UNKNOWN", "template_id": template_id, "error": "No local verified procedure matches this template ID.", "certainty": "not_established"}

    def sync_from_github(self, repository: str, branch: str, token: str = "") -> dict[str, Any]:
        _validate_repository(repository)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token: headers["Authorization"] = "Bearer " + token
        url = "https://api.github.com/repos/" + repository + "/contents/library/templates?ref=" + urllib.parse.quote(branch, safe="")
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response: listing = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ChildError("Home sync failed with HTTP " + str(exc.code)) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ChildError("Home sync failed: " + str(exc)) from exc
        if not isinstance(listing, list): raise ChildError("Home template directory response was not a file list")
        added, unchanged = 0, 0
        local = self.local_templates()
        for item in listing:
            if not isinstance(item, dict) or item.get("type") != "file" or not str(item.get("name", "")).endswith(".json"): continue
            download_url = item.get("download_url")
            if not download_url: continue
            download = urllib.request.Request(download_url, headers=headers)
            try:
                with urllib.request.urlopen(download, timeout=30) as response: candidate = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                raise ChildError("Could not download Home template " + str(item.get("name"))) from exc
            existing = next((x for x in local if x.get("id") == candidate.get("id")), None)
            if existing:
                if template_fingerprint(existing) == template_fingerprint(candidate):
                    unchanged += 1
                    continue
                raise ChildError("Home template ID conflicts with a different local procedure: " + str(candidate.get("id")))
            report = self.verifier.verify(candidate, local)
            if not report.passed: raise ChildError("Home supplied a template that failed local verification: " + "; ".join(report.issues))
            path = self.templates_dir / (candidate["id"] + ".json")
            path.write_bytes(canonical_json(candidate))
            local.append(candidate)
            added += 1
        return {"status": "SYNCED", "added": added, "unchanged": unchanged, "library_count": len(local), "home_branch": branch}

    def submit(self, template_id: str, github_token: str, repository: str, base_branch: str, hmac_secret: str) -> dict[str, Any]:
        state = self._state()
        if state.get("paused"): raise ChildError("child is paused after 100 consecutive rejected proposals")
        candidate = next((x for x in self.local_templates() if x.get("id") == template_id), None)
        if not candidate: raise ChildError("template is not present in the local child library")
        signed = sign_template(candidate, self.child_id, hmac_secret)
        _validate_repository(repository)
        branch = "legend-" + re.sub(r"[^a-z0-9-]", "-", self.child_id.lower())[:30] + "-" + uuid.uuid4().hex[:10]
        api = "https://api.github.com"
        headers = {"Authorization": "Bearer " + github_token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        base_ref = _github_request(api + "/repos/" + repository + "/git/ref/heads/" + urllib.parse.quote(base_branch, safe=""), headers)
        _github_request(api + "/repos/" + repository + "/git/refs", headers, {"ref": "refs/heads/" + branch, "sha": base_ref["object"]["sha"]})
        path = "library/templates/" + template_id + ".json"
        content = base64.b64encode(canonical_json(signed)).decode("ascii")
        _github_request(api + "/repos/" + repository + "/contents/" + urllib.parse.quote(path, safe="/"), headers, {"message": "Legend child proposal: " + template_id, "content": content, "branch": branch})
        pull = _github_request(api + "/repos/" + repository + "/pulls", headers, {
            "title": "Legend template proposal: " + template_id, "head": branch, "base": base_branch,
            "body": "Automated child submission. Child ID: " + self.child_id + ". Template: " + template_id + ". Only T-Lang and a signed submission envelope are included. Home verification is required before merge.",
        })
        return {"status": "SUBMITTED", "pull_request": pull.get("html_url"), "number": pull.get("number"), "branch": branch}


def _validate_repository(repository: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository): raise ChildError("repository must have owner/name format")


def _github_request(url: str, headers: dict[str, str], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={**headers, "Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response: return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ChildError("GitHub API returned HTTP " + str(exc.code) + ": " + detail) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ChildError("GitHub API request failed: " + str(exc)) from exc


def load_child_from_env() -> ChildEngine:
    import os
    return ChildEngine(os.getenv("LEGEND_CHILD_ID", "local-child"), os.getenv("LEGEND_CHILD_WORKSPACE", ".legend-child"))
