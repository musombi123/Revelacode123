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
from typing import Any
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .builtins import execute_learned_input
from .github_home import GitHubHomeError, fetch_method_index, submit_template
from .home_index import find_duplicate
from .store import TemplateStore
from .vault import (
    VaultError,
    inspect_vault,
    mark_pending_home_submission,
    pending_duplicate_reason,
    promote_pending_home_methods,
    write_pending_method,
    write_verified_artifact_to_vault,
)
from .vlang import TemplateError, dump_record, validate_template
from .tools import ARTIFACT_ROOT, execute_tool_dict

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

# This store contains rejection counters and only Child-originated methods whose Home approval was confirmed through the metadata index.
STORE = TemplateStore(os.getenv("VORAX_DB_PATH", "/tmp/vorax-runtime.sqlite3"))
_HOME_INDEX = None
_HOME_INDEX_AT = 0.0
_HOME_INDEX_STATUS = {"status": "not_checked", "methods_total": 0}
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
    required_secrets = {
        "VORAX_HOME_READ_TOKEN": HOME_READ_TOKEN,
        "VORAX_HOME_WRITE_TOKEN": HOME_WRITE_TOKEN,
        "HF_VAULT_TOKEN": os.getenv("HF_VAULT_TOKEN", "").strip(),
        "HF_INFERENCE_TOKEN": os.getenv("HF_INFERENCE_TOKEN", "").strip(),
        "VORAX_TEACHER_ATTESTATION_KEY": os.getenv("VORAX_TEACHER_ATTESTATION_KEY", "").strip(),
    }
    missing = [name for name, value in required_secrets.items() if not value]
    if missing:
        raise HTTPException(503, "Runtime credentials are incomplete: " + ", ".join(missing))
    return CHILD_ID


def _refresh_home_index(force: bool = False) -> tuple[dict | None, dict]:
    global _HOME_INDEX, _HOME_INDEX_AT, _HOME_INDEX_STATUS
    if not force and _HOME_INDEX is not None and time.monotonic() - _HOME_INDEX_AT < 60:
        return _HOME_INDEX, dict(_HOME_INDEX_STATUS)
    if not HOME_READ_TOKEN:
        _HOME_INDEX = None
        _HOME_INDEX_STATUS = {"status": "credentials_missing", "methods_total": 0}
        _HOME_INDEX_AT = time.monotonic()
        return None, dict(_HOME_INDEX_STATUS)
    try:
        # Read only Home's metadata index; never fetch a Home V-Lang method body.
        _HOME_INDEX = fetch_method_index(HOME_REPO, HOME_BRANCH, HOME_READ_TOKEN)
        _HOME_INDEX_STATUS = {
            "status": "metadata_index_read",
            "methods_total": _HOME_INDEX["methods_total"],
            "source": f"{HOME_REPO}/templates/library/index.json",
        }
    except Exception as exc:
        _HOME_INDEX = None
        _HOME_INDEX_STATUS = {
            "status": "unavailable",
            "methods_total": 0,
            "reason": type(exc).__name__,
        }
    _HOME_INDEX_AT = time.monotonic()
    return _HOME_INDEX, dict(_HOME_INDEX_STATUS)


def _reconcile_home_methods(home_index: dict) -> dict:
    """Load only Child-originated method bytes already in Vault pending after Home confirms their metadata."""
    result = promote_pending_home_methods(home_index)
    methods = result.pop("promoted_methods", [])
    loaded_ids = []
    if not isinstance(methods, list):
        raise VaultError("Vault reconciliation returned an invalid promoted method list")
    for record in methods:
        if not isinstance(record, dict):
            raise VaultError("Vault reconciliation returned a malformed V-Lang method")
        # These bytes came from the Runtime-written Vault pending area, never from GitHub Home.
        STORE.add(record)
        loaded_ids.append(record["id"])
    result["methods_loaded_into_runtime"] = len(loaded_ids)
    result["loaded_method_ids"] = loaded_ids
    return result


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
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
        content = re.sub(r"\s*```$", "", content)
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "Teacher response was not valid JSON V-Lang") from exc
    if isinstance(value, dict) and isinstance(value.get("candidate"), dict):
        value = value["candidate"]
    if not isinstance(value, dict):
        raise HTTPException(422, "Teacher response did not contain a V-Lang method object")
    return value


def _verify_teacher_attestation(
    teacher_response: str,
    attestation: dict,
    expected_model_id: str,
) -> dict:
    """Verify Teacher-signed provider provenance before trusting a Child-supplied method."""
    if not isinstance(teacher_response, str) or not teacher_response.strip():
        raise HTTPException(422, "teacher_response is required")
    if not isinstance(attestation, dict) or attestation.get("algorithm") != "hmac-sha256":
        raise HTTPException(403, "A signed Teacher attestation is required")
    payload = attestation.get("payload")
    signature = attestation.get("signature")
    required = {
        "model_id", "provider_used", "remote_inference", "kaggle_t4_used",
        "weights_downloaded", "response_sha256", "request_id", "issued_at",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise HTTPException(403, "Teacher attestation payload has an invalid schema")
    secret = os.getenv("VORAX_TEACHER_ATTESTATION_KEY", "").strip()
    if len(secret) < 32:
        raise HTTPException(503, "Runtime Teacher attestation key is not configured")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise HTTPException(403, "Teacher attestation signature is invalid")
    if payload.get("model_id") != expected_model_id:
        raise HTTPException(403, "Teacher attestation model ID does not match the requested model")
    if payload.get("remote_inference") is not True:
        raise HTTPException(403, "Teacher attestation does not prove remote inference")
    if payload.get("kaggle_t4_used") is not False or payload.get("weights_downloaded") is not False:
        raise HTTPException(403, "Teacher attestation violates the weight-free remote-inference policy")
    if not isinstance(payload.get("provider_used"), str) or not payload["provider_used"]:
        raise HTTPException(403, "Teacher attestation provider metadata is missing")
    issued_at = payload.get("issued_at")
    if not isinstance(issued_at, int) or isinstance(issued_at, bool):
        raise HTTPException(403, "Teacher attestation timestamp is invalid")
    now = int(time.time())
    if issued_at > now + 60 or now - issued_at > 900:
        raise HTTPException(403, "Teacher attestation has expired")
    response_sha = hashlib.sha256(teacher_response.encode("utf-8")).hexdigest()
    if payload.get("response_sha256") != response_sha:
        raise HTTPException(403, "Teacher attestation does not match the supplied response")
    return payload


def _validate_teacher_candidate(
    teacher_response: str,
    attestation: dict,
    candidate: dict,
    model_id: str,
) -> dict:
    proof = _verify_teacher_attestation(teacher_response, attestation, model_id)
    extracted = _parse_candidate(teacher_response)
    if not isinstance(candidate, dict):
        raise HTTPException(422, "method must be a V-Lang object")
    expected = json.dumps(extracted, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    supplied = json.dumps(candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if expected != supplied:
        raise HTTPException(403, "Child-submitted method does not match the Teacher-signed response")
    return proof


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
    home_index, index_status = _refresh_home_index(force=True)
    if home_index is None:
        raise HTTPException(503, "Home metadata-only index is unavailable; refusing duplicate-blind submission")
    try:
        # On a real learning/verification request, reconcile earlier pending methods
        # whose IDs and metadata have since appeared in Home. No Home method body is fetched.
        home_reconciliation = _reconcile_home_methods(home_index)
    except VaultError as exc:
        raise HTTPException(503, "Runtime could not reconcile Home-approved Vault methods: " + str(exc)) from exc
    duplicate = find_duplicate(home_index, record)
    if duplicate:
        raise HTTPException(409, {
            "status": "unknown",
            "reason": duplicate,
            "home_index_methods": home_index["methods_total"],
            "method_body_from_home_loaded": False,
        })

    try:
        validate_template(record, run_tests=True)
        canonical = dump_record(record).encode("utf-8")
        method_sha = hashlib.sha256(canonical).hexdigest()
        # Pending candidates are deliberately not added to the executable catalog.
        # Runtime never executes pending proposals; Home approval remains a separate gate.
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

    try:
        pending_duplicate = pending_duplicate_reason(record)
    except VaultError as exc:
        raise HTTPException(503, "Vault pending index could not be inspected: " + str(exc)) from exc
    if pending_duplicate:
        _log(
            "method_already_pending",
            child_id=child_id,
            x=_redact_x(x_value),
            method=record["id"],
            verification="duplicate_in_vault_pending",
            duplicate_reason=pending_duplicate,
        )
        raise HTTPException(409, {
            "status": "unknown",
            "reason": pending_duplicate,
            "home_certification": "pending",
        })

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
            "vault_tracking": "pending",
        }
    except Exception as exc:
        home_submission = {
            "status": "pending_retry",
            "reason": type(exc).__name__,
        }
    else:
        # Preserve the already-created Home PR receipt if the separate Vault
        # bookkeeping update has a transient failure.
        try:
            mark_pending_home_submission(record["id"], submitted["pull_request"])
            home_submission["vault_tracking"] = "linked_to_home_pr"
        except Exception as exc:
            home_submission["vault_tracking"] = "update_failed"
            home_submission["vault_tracking_error"] = type(exc).__name__

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
        "home_reconciliation": home_reconciliation,
        "home_submission": home_submission,
    }


@app.on_event("startup")
def startup_reconcile_home_approved_methods():
    """Rehydrate only Child-originated Vault methods whose metadata is confirmed by Home."""
    index, _ = _refresh_home_index(force=True)
    if index is None or not os.getenv("HF_VAULT_TOKEN", "").strip():
        return
    try:
        receipt = _reconcile_home_methods(index)
        _log(
            "runtime_startup_vault_reconcile",
            child_id=CHILD_ID,
            x="Startup Home metadata reconciliation",
            method="multiple" if receipt.get("loaded_method_ids") else None,
            verification=receipt.get("status"),
            loaded_method_ids=receipt.get("loaded_method_ids", []),
        )
    except Exception as exc:
        _log(
            "runtime_startup_vault_reconcile_failed",
            child_id=CHILD_ID or None,
            x="Startup Home metadata reconciliation",
            method=None,
            verification="unavailable",
            reason=type(exc).__name__,
        )


@app.get("/health")
def health():
    home_index, index_status = _refresh_home_index()
    vault = {"status": "not_configured", "repo": VAULT_REPO, "repo_type": "dataset"}
    if os.getenv("HF_VAULT_TOKEN", "").strip():
        try:
            vault = inspect_vault()
        except Exception as exc:
            vault = {
                "status": "unavailable",
                "repo": VAULT_REPO,
                "repo_type": "dataset",
                "reason": type(exc).__name__,
            }
    required = {
        "child_key": bool(CHILD_KEY),
        "home_read_token": bool(HOME_READ_TOKEN),
        "home_write_token": bool(HOME_WRITE_TOKEN),
        "vault_token": bool(os.getenv("HF_VAULT_TOKEN", "").strip()),
        "inference_provider_token": bool(os.getenv("HF_INFERENCE_TOKEN", "").strip()),
        "teacher_attestation_key": len(os.getenv("VORAX_TEACHER_ATTESTATION_KEY", "").strip()) >= 32,
    }
    return {
        "status": "ok",
        "service": "runtime",
        "engine": "VORAX",
        "vlang": "vlang/1",
        "shared_runtime": True,
        "home_repository": HOME_REPO,
        "home_index": index_status,
        "home_method_bodies_loaded": False,
        "vault": vault,
        "required_secrets": required,
        "ready": all(required.values()) and home_index is not None and vault.get("status") == "verified",
        "weights_downloaded": False,
        "local_model_loaded": False,
    }


@app.get("/vorax/home-index")
def get_home_index(
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    child_id = _require_child(x_child_id, x_child_key)
    index, _ = _refresh_home_index(force=True)
    if index is None:
        raise HTTPException(503, "Home metadata-only index is unavailable")
    _log(
        "home_metadata_index_read",
        child_id=child_id,
        x="Home metadata index",
        method=None,
        verification="metadata_only",
        index_methods=index["methods_total"],
    )
    return {**index, "method_bodies_included": False}


@app.post("/vorax/x")
def input_x(
    body: dict,
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    child_id = _require_child(body.get("child_id") or x_child_id, body.get("api_key") or x_child_key)
    value = body.get("x")
    if not isinstance(value, str) or not value.strip() or len(value) > 12_000:
        raise HTTPException(422, "x must be non-empty text of at most 12000 characters")

    home_index, index_status = _refresh_home_index(force=True)
    if home_index is None:
        raise HTTPException(503, "Home metadata-only index is unavailable; Runtime cannot confirm approved methods")
    try:
        home_reconciliation = _reconcile_home_methods(home_index)
    except VaultError as exc:
        raise HTTPException(503, "Runtime could not reconcile Home-approved methods from Vault: " + str(exc)) from exc

    result = execute_learned_input(value, STORE.all(), tool_executor=execute_tool_dict)
    status = result.get("status", "unknown")
    if status not in {"verified", "unknown", "blocked"}:
        status = "unknown"
    method_id = result.get("method")
    answer = result.get("answer")
    artifact_sha = result.get("artifact_sha256")
    vault_artifact = None

    if status == "verified" and isinstance(answer, dict) and isinstance(answer.get("artifact"), str):
        artifact_name = answer["artifact"]
        artifact_path = (ARTIFACT_ROOT / artifact_name).resolve()
        if ARTIFACT_ROOT.resolve() not in artifact_path.parents:
            raise HTTPException(500, "Verified artifact path escaped Runtime storage")
        try:
            vault_artifact = write_verified_artifact_to_vault(
                str(artifact_path),
                child_id=child_id,
                x_value=_redact_x(value),
                provider=str(answer.get("provider") or "unknown"),
            )
            artifact_sha = vault_artifact["sha256"]
        except VaultError as exc:
            status = "unknown"
            artifact_sha = None
            result["reason"] = "remote_artifact_verified_but_vault_persistence_failed"
            _log(
                "artifact_vault_write_failed",
                child_id=child_id,
                x=_redact_x(value),
                method=method_id,
                verification="artifact_not_persisted",
                reason=str(exc)[:240],
            )

    verification = result.get("verification") or {
        "authority": "shared_vorax_runtime",
        "home_approved": False,
        "child_self_certification": False,
    }
    response = {
        "status": status,
        "engine": "VORAX",
        "method": method_id,
        "answer": answer if status == "verified" else None,
        "artifact_sha256": artifact_sha,
        "artifact_vault": vault_artifact,
        "verification": {
            **verification,
            "home_method_bodies_loaded": False,
            "home_index_status": index_status["status"],
            "home_reconciliation": {
                "status": home_reconciliation.get("status"),
                "methods_loaded_into_runtime": home_reconciliation.get("methods_loaded_into_runtime", 0),
            },
            "child_self_certification": False,
        },
        "home_index": index_status,
        "reason": result.get("reason"),
    }
    _log(
        "input_x",
        child_id=child_id,
        x=_redact_x(value),
        method=method_id,
        verification=status,
        artifact_sha256=artifact_sha,
    )
    return response


@app.post("/vorax/verify")
def verify_method(
    body: dict,
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    child_id = _require_child(body.get("child_id") or x_child_id, body.get("api_key") or x_child_key)
    method = body.get("method")
    if not isinstance(method, dict):
        raise HTTPException(422, "method must be a V-Lang object")
    teacher_id = str(body.get("teacher_model_id") or "").strip()
    if not teacher_id:
        raise HTTPException(422, "teacher_model_id is required")
    teacher_response = body.get("teacher_response")
    teacher_attestation = body.get("teacher_attestation")
    _validate_teacher_candidate(teacher_response, teacher_attestation, method, teacher_id)
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
    teacher_id = teacher.get("model_id", model_id)
    candidate = _parse_candidate(teacher["response"])
    _validate_teacher_candidate(
        teacher["response"],
        teacher.get("attestation"),
        candidate,
        teacher_id,
    )
    return _verify_and_submit(candidate, child_id, teacher_id, body.task)

@app.post("/vorax/vault/reconcile")
def reconcile_vault(
    body: dict | None = None,
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    payload = body or {}
    child_id = _require_child(payload.get("child_id") or x_child_id, payload.get("api_key") or x_child_key)
    index, _ = _refresh_home_index(force=True)
    if index is None:
        raise HTTPException(503, "Home metadata-only index is unavailable")
    try:
        result = _reconcile_home_methods(index)
    except VaultError as exc:
        raise HTTPException(503, "Vault reconciliation failed: " + str(exc)) from exc
    _log(
        "vault_reconcile",
        child_id=child_id,
        x="Home metadata index reconciliation",
        method="multiple" if result["promoted_method_ids"] else None,
        verification=result["status"],
        promoted_methods=result["promoted_method_ids"],
    )
    return result

