"""Weight-free VORAX Runtime Service over the shared V-Lang interpreter."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .github_home import GitHubHomeError, submit_template, sync_library
from .input_x import handle_input_x
from .store import TemplateStore
from .tools import execute_tool_dict
from .vault import VaultError, inspect_vault, mark_pending_home_submission, write_pending_method
from .vlang import TemplateError, dump_record, parse_document, validate_template

ROOT = Path(__file__).resolve().parents[1]
HOME_REPO = os.getenv("VORAX_HOME_REPO", "musombi123/Revelacode123")
HOME_BRANCH = os.getenv("VORAX_HOME_BRANCH", "main")
VAULT_REPO = os.getenv("HF_VAULT_REPO", "Musombi/revelacode-vorax-vault")
CHILD_ID = os.getenv("VORAX_CHILD_ID", "kaggle-child").strip()
CHILD_KEY = os.getenv("VORAX_KAGGLE_CHILD_API_KEY", "").strip()
HOME_READ_TOKEN = os.getenv("VORAX_HOME_READ_TOKEN", "").strip()
HOME_WRITE_TOKEN = os.getenv("VORAX_HOME_WRITE_TOKEN", "").strip()
TEACHER_URL = os.getenv(
    "VORAX_TEACHER_URL",
    "https://musombi-vorax-teacher-service.hf.space",
).rstrip("/")
TEACHER_MODEL = os.getenv("VORAX_TEACHER_MODEL", "").strip()
LOGGER = logging.getLogger("vorax.runtime-service")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

STORE = TemplateStore(os.getenv("VORAX_DB_PATH", "/tmp/vorax-runtime.sqlite3"))
_HOME_SYNC_AT = 0.0
_HOME_SYNC = {"status": "not_checked", "new_methods": 0}
app = FastAPI(
    title="VORAX Runtime Service",
    version="1.0.0",
    description="Shared VORAX/V-Lang runtime with authenticated Child intake and Home-first verification.",
)


class LearnRequest(BaseModel):
    task: str = Field(min_length=1, max_length=500)
    model_id: str | None = Field(default=None, max_length=220)
    inputs: dict[str, Any] = Field(default_factory=dict)
    triggers: list[str] = Field(default_factory=list, max_length=12)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(event: str, **fields) -> None:
    LOGGER.info(json.dumps({"event": event, "timestamp": _now(), **fields}, ensure_ascii=False, default=str))


def _redact_x(value: str) -> str:
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]{12,}", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)((?:api[_ -]?key|password|secret|token)\s*[:=]\s*)\S+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[EMAIL]", value)
    return value[:240]


def _require_child(child_id: str | None, child_key: str | None) -> str:
    if os.getenv("VORAX_CHILD_ROLE", "hf-runtime").strip().lower() != "hf-runtime":
        raise HTTPException(503, "This endpoint is not running as the HF Runtime Child")
    if not CHILD_KEY:
        raise HTTPException(503, "VORAX_KAGGLE_CHILD_API_KEY is not configured")
    if not child_id or child_id != CHILD_ID or not child_key or not hmac.compare_digest(child_key, CHILD_KEY):
        raise HTTPException(403, "Authenticated VORAX Child required")
    if STORE.child_blocked(CHILD_ID):
        raise HTTPException(403, {"status": "blocked", "reason": "Child is blocked by the Home rejection policy"})
    if not HOME_READ_TOKEN or not HOME_WRITE_TOKEN:
        raise HTTPException(503, "VORAX Home read/write credentials are not configured")
    return CHILD_ID


def _seed_store() -> None:
    folder = ROOT / "templates" / "library"
    if not folder.exists():
        return
    for pattern in ("*.vlang", "*.tlang"):
        for path in folder.rglob(pattern):
            for record in parse_document(path.read_text(encoding="utf-8")):
                STORE.add(record)


def _refresh_home(force: bool = False) -> dict:
    global _HOME_SYNC_AT, _HOME_SYNC
    if not force and time.monotonic() - _HOME_SYNC_AT < 60:
        return dict(_HOME_SYNC)
    if not HOME_READ_TOKEN:
        _HOME_SYNC = {"status": "credentials_missing", "new_methods": 0}
        _HOME_SYNC_AT = time.monotonic()
        return dict(_HOME_SYNC)
    try:
        records = sync_library(HOME_REPO, HOME_BRANCH, token=HOME_READ_TOKEN)
        added = 0
        for record in records:
            before = STORE.get(record["id"])
            STORE.add(record)
            if before is None:
                added += 1
        _HOME_SYNC = {"status": "synced", "new_methods": added, "home_methods": len(records)}
    except Exception as exc:
        _HOME_SYNC = {"status": "cached", "reason": type(exc).__name__, "home_methods": len(STORE.all())}
    _HOME_SYNC_AT = time.monotonic()
    return dict(_HOME_SYNC)


def _artifact_sha256(result: dict) -> str | None:
    answer = result.get("answer")
    candidate = answer if isinstance(answer, dict) else result
    value = candidate.get("sha256") if isinstance(candidate, dict) else None
    if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value):
        return value
    return None


def _teacher_ask(prompt: str, model_id: str, child_id: str) -> dict:
    if not TEACHER_URL.startswith("https://"):
        raise HTTPException(503, "VORAX_TEACHER_URL must use HTTPS")
    payload = json.dumps({"prompt": prompt, "model_id": model_id}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        TEACHER_URL + "/teacher/ask",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-child-id": child_id,
            "x-child-key": CHILD_KEY,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=100) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise HTTPException(502, "Teacher response exceeded the allowed size")
            teacher = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise HTTPException(502, "Teacher rejected the authenticated Child request") from exc
        raise HTTPException(502, f"Teacher Service returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "Teacher Service request failed: " + type(exc).__name__) from exc
    if teacher.get("remote_inference") is not True or teacher.get("kaggle_t4_used") is not False:
        raise HTTPException(502, "Teacher response did not provide the required remote-inference provenance")
    return teacher


def _parse_candidate(text: str) -> dict:
    content = text.strip()
    if content.startswith("```"):
        content = re.sub(r"^\`\`\`(?:json)?\s*", "", content, flags=re.I)
        content = re.sub(r"\s*\`\`\`$", "", content)
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "Teacher response was not valid JSON V-Lang") from exc
    if isinstance(value, dict) and isinstance(value.get("candidate"), dict):
        value = value["candidate"]
    if not isinstance(value, dict):
        raise HTTPException(422, "Teacher response did not contain a V-Lang method object")
    return value


def _verify_and_submit(candidate: dict, child_id: str, teacher_id: str, x_value: str) -> dict:
    if not isinstance(candidate, dict):
        raise HTTPException(422, "method must be a V-Lang object")
    record = copy.deepcopy(candidate)
    # Child/Teacher provenance is assigned by the trusted Runtime, never accepted from the payload.
    record["provenance"] = {
        "kind": "teacher",
        "child_id": child_id,
        "teacher": (teacher_id or "hf-teacher-service")[:64],
    }
    try:
        validate_template(record, run_tests=True)
        canonical = dump_record(record).encode("utf-8")
        method_sha = hashlib.sha256(canonical).hexdigest()
        STORE.add(record)
    except (TemplateError, ValueError) as exc:
        STORE.record_failure(child_id)
        _log(
            "method_rejected",
            child_id=child_id,
            x=_redact_x(x_value),
            method=str(record.get("id", ""))[:100],
            verification="failed",
            reason=type(exc).__name__,
        )
        raise HTTPException(422, {"status": "blocked", "verification": "failed", "reason": str(exc)[:400]}) from exc

    verification = {
        "status": "runtime_tests_passed",
        "authority": "trusted_vorax_vlang_interpreter",
        "tests_passed": len(record["tests"]),
        "method_sha256": method_sha,
        "home_certification": "pending",
        "child_self_certification": False,
    }
    try:
        vault_receipt = write_pending_method(
            record,
            child_id=child_id,
            verification=verification,
        )
    except VaultError as exc:
        _log(
            "method_verified_vault_write_failed",
            child_id=child_id,
            x=_redact_x(x_value),
            method=record["id"],
            verification="runtime_tests_passed_vault_failed",
            reason=str(exc)[:240],
        )
        raise HTTPException(503, "Method passed Runtime tests but Vault pending write failed; no Home PR was opened") from exc

    home_submission = {"status": "not_submitted"}
    try:
        if not HOME_WRITE_TOKEN:
            raise GitHubHomeError("VORAX_HOME_WRITE_TOKEN is not configured")
        submitted = submit_template(
            HOME_REPO,
            HOME_BRANCH,
            child_id,
            record,
            HOME_WRITE_TOKEN,
        )
        home_submission = {
            "status": "submitted_for_home_review",
            "pull_request": submitted["pull_request"],
            "number": submitted["number"],
            "branch": submitted["branch"],
        }
        mark_pending_home_submission(record["id"], submitted["pull_request"])
    except Exception as exc:
        home_submission = {
            "status": "pending_retry",
            "reason": type(exc).__name__,
        }

    STORE.record_success(child_id)
    _log(
        "method_runtime_verified",
        child_id=child_id,
        x=_redact_x(x_value),
        method=record["id"],
        verification="runtime_tests_passed_home_review_pending",
        method_sha256=method_sha,
        home_pr=home_submission.get("pull_request"),
    )
    return {
        "status": "verified",
        "method": record["id"],
        "method_sha256": method_sha,
        "artifact_sha256": None,
        "verification": verification,
        "vault": vault_receipt,
        "home_submission": home_submission,
    }


@app.on_event("startup")
def startup():
    _seed_store()
    _refresh_home(force=True)


@app.get("/health")
def health():
    vault = {"status": "not_configured", "repo": VAULT_REPO, "repo_type": "dataset"}
    if os.getenv("HF_VAULT_TOKEN", "").strip():
        try:
            vault = inspect_vault()
        except Exception as exc:
            vault = {"status": "unavailable", "repo": VAULT_REPO, "repo_type": "dataset", "reason": type(exc).__name__}
    required = {
        "child_key": bool(CHILD_KEY),
        "home_read_token": bool(HOME_READ_TOKEN),
        "home_write_token": bool(HOME_WRITE_TOKEN),
        "vault_token": bool(os.getenv("HF_VAULT_TOKEN", "").strip()),
    }
    return {
        "status": "ok",
        "service": "runtime",
        "engine": "VORAX",
        "vlang": "vlang/1",
        "shared_runtime": True,
        "home_repository": HOME_REPO,
        "home_sync": dict(_HOME_SYNC),
        "vault": vault,
        "required_secrets": required,
        "ready": all(required.values()) and vault.get("status") == "verified",
        "weights_downloaded": False,
        "local_model_loaded": False,
    }


@app.post("/vorax/x")
def input_x(
    body: dict,
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    child_id = _require_child(x_child_id, x_child_key)
    value = body.get("x")
    if not isinstance(value, str) or not value.strip() or len(value) > 12_000:
        raise HTTPException(422, "x must be non-empty text of at most 12000 characters")
    if "api_key" in body:
        raise HTTPException(400, "Do not put API keys in JSON; authenticate using x-child-key")
    home_sync = _refresh_home()
    result = handle_input_x(value, STORE.all(), tool_executor=execute_tool_dict)
    status = result.get("status", "unknown")
    method_id = result.get("template_id")
    answer = result.get("answer")
    artifact_sha = _artifact_sha256(result) if status == "verified" else None
    verification = {
        "status": "runtime_method_passed" if status == "verified" else status,
        "authority": "shared_vorax_runtime",
        "home_sync": home_sync.get("status"),
        "child_self_certification": False,
    }
    _log(
        "input_x",
        child_id=child_id,
        x=_redact_x(value),
        method=method_id,
        verification=verification["status"],
        artifact_sha256=artifact_sha,
    )
    return {
        "status": status,
        "engine": "VORAX",
        "method": method_id,
        "answer": answer,
        "artifact_sha256": artifact_sha,
        "verification": verification,
        "home_sync": home_sync,
    }


@app.post("/vorax/verify")
def verify_method(
    body: dict,
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    child_id = _require_child(x_child_id, x_child_key)
    method = body.get("method")
    if not isinstance(method, dict):
        raise HTTPException(422, "method must be a V-Lang object")
    teacher_id = str(body.get("teacher_model_id") or "hf-teacher-service")
    x_value = str(body.get("x") or method.get("task") or method.get("id") or "V-Lang verification")
    return _verify_and_submit(method, child_id, teacher_id, x_value)


@app.post("/vorax/learn")
def learn_method(
    body: LearnRequest,
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    child_id = _require_child(x_child_id, x_child_key)
    model_id = (body.model_id or TEACHER_MODEL).strip()
    if not model_id:
        raise HTTPException(503, "VORAX_TEACHER_MODEL is not configured")
    prompt = (
        "Return exactly one JSON V-Lang v1 method object, with keys version,id,domain,task,"
        "triggers,inputs,steps,result,tests,provenance. Use only the registered V-Lang operations "
        "or a registered CALL TOOL contract. Never include Python, JavaScript, shell, URLs, secrets, "
        "weights, or claims that an artifact has been generated. Include at least two executable "
        "test vectors with exact expected outputs. The trusted Runtime will execute every test. "
        "For CALL TOOL, tests only check the registered tool contract and do not prove media generation.\n"
        + json.dumps({
            "task": body.task,
            "inputs": body.inputs,
            "triggers": body.triggers or [body.task[:48]],
        }, ensure_ascii=False)
    )
    teacher = _teacher_ask(prompt, model_id, child_id)
    candidate = _parse_candidate(teacher["response"])
    return _verify_and_submit(
        candidate,
        child_id,
        teacher.get("model_id", model_id),
        body.task,
    )
