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
from datetime import datetime, timedelta, timezone
from typing import Any
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .builtins import execute_learned_input
from .github_home import (
    GitHubHomeError,
    create_home_pull_request,
    fetch_method_index,
    is_merged_home_pull_request,
    create_home_report_pull_request,
)
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
    read_weekly_cycle_manifest,
    read_weekly_hunter_report,
    read_weekly_report,
    write_weekly_cycle_manifest,
    write_weekly_hunter_report,
    write_weekly_report,
    claim_weekly_proposal_digest,
    pending_home_proposals,
    read_home_metadata_cache,
    write_home_metadata_cache,
    pending_method_ids,
    pending_method_coverage,
    pending_method_by_sha256,
    list_weekly_cycle_manifests,
)
from .vlang import TemplateError, dump_record, validate_template
from .tools import ARTIFACT_ROOT, execute_tool_dict
from .network import NetworkError, PeerHealthMonitor, request_json
from .weekly_cycle import (
    WeeklyCycleError,
    aggregate_hunter_reports,
    build_cycle_manifest,
    prepare_cycle_retry,
    update_hunter_assignment,
    validate_hunter_report,
    validate_candidate_for_curriculum,
    validate_cycle_id,
    timestamp,
)

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
TEACHER_MONITOR = PeerHealthMonitor(TEACHER_URL + "/health", "teacher", interval_seconds=60)
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

    prior_index = _HOME_INDEX
    try:
        if not HOME_READ_TOKEN:
            raise GitHubHomeError("Home read credentials are missing")
        # Home remains the source of truth; Runtime reads only the metadata index.
        fresh_index = fetch_method_index(HOME_REPO, HOME_BRANCH, HOME_READ_TOKEN)
        _HOME_INDEX = fresh_index
        _HOME_INDEX_STATUS = {
            "status": "metadata_index_read",
            "methods_total": fresh_index["methods_total"],
            "source": f"{HOME_REPO}/templates/library/index.json",
            "stale": False,
        }
        try:
            cache_receipt = write_home_metadata_cache(fresh_index)
            _HOME_INDEX_STATUS["vault_cache"] = cache_receipt.get("status")
        except VaultError as cache_exc:
            _HOME_INDEX_STATUS["vault_cache"] = "unavailable"
            _HOME_INDEX_STATUS["cache_error"] = type(cache_exc).__name__
    except Exception as exc:
        cached_index = prior_index
        cache_kind = "runtime_memory_snapshot"
        if cached_index is None:
            try:
                cached_index = read_home_metadata_cache()
                cache_kind = "vault_snapshot"
            except VaultError:
                cached_index = None
        if cached_index is not None:
            _HOME_INDEX = cached_index
            _HOME_INDEX_STATUS = {
                "status": "cached_stale",
                "methods_total": cached_index["methods_total"],
                "source": f"{HOME_REPO}/templates/library/index.json",
                "cache_kind": cache_kind,
                "stale": True,
                "reason": type(exc).__name__,
            }
        else:
            _HOME_INDEX = None
            _HOME_INDEX_STATUS = {
                "status": "unavailable",
                "methods_total": 0,
                "reason": type(exc).__name__,
                "stale": True,
            }
    _HOME_INDEX_AT = time.monotonic()
    return _HOME_INDEX, dict(_HOME_INDEX_STATUS)


def _reconcile_home_methods(home_index: dict) -> dict:
    """Load only Vault-pending methods whose exact Home PR is merged and digest-confirmed."""
    result = promote_pending_home_methods(
        home_index,
        merged_pr_verifier=lambda pr_url: is_merged_home_pull_request(
            HOME_REPO,
            pr_url,
            HOME_BRANCH,
            HOME_READ_TOKEN,
        ),
    )
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
    try:
        teacher = request_json(
            TEACHER_URL + "/teacher/ask",
            method="POST",
            payload={"prompt": prompt, "model_id": model_id},
            headers={"x-child-id": child_id, "x-child-key": CHILD_KEY},
            timeout=30,
            attempts=3,
            max_response_bytes=2 * 1024 * 1024,
            breaker=TEACHER_MONITOR.breaker,
        )
    except NetworkError as exc:
        message = str(exc)
        if "HTTP 401" in message or "HTTP 403" in message:
            raise HTTPException(502, "Teacher rejected the authenticated Child request") from exc
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
    *,
    allow_expired: bool = False,
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
    if issued_at > now + 60:
        raise HTTPException(403, "Teacher attestation timestamp is in the future")
    if not allow_expired and now - issued_at > 900:
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
    *,
    allow_expired: bool = False,
) -> dict:
    proof = _verify_teacher_attestation(
        teacher_response,
        attestation,
        model_id,
        allow_expired=allow_expired,
    )
    extracted = _parse_candidate(teacher_response)
    if not isinstance(candidate, dict):
        raise HTTPException(422, "method must be a V-Lang object")
    expected = json.dumps(extracted, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    supplied = json.dumps(candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if expected != supplied:
        raise HTTPException(403, "Child-submitted method does not match the Teacher-signed response")
    return proof


def _verify_and_submit(
    candidate: dict,
    child_id: str,
    teacher_id: str,
    x_value: str,
    *,
    cycle_id: str | None = None,
    hunter_id: str | None = None,
) -> dict:
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
        if cycle_id:
            try:
                existing_pending = pending_method_by_sha256(method_sha)
            except VaultError as exc:
                raise HTTPException(503, "Runtime could not verify a cached pending proposal") from exc
            if existing_pending is not None:
                try:
                    claim = claim_weekly_proposal_digest(method_sha, cycle_id, hunter_id)
                except VaultError as exc:
                    raise HTTPException(503, "Runtime weekly proposal de-duplication is unavailable") from exc
                if claim.get("idempotent_retry") is True:
                    entry = existing_pending["entry"]
                    home_submission = {
                        "status": "submitted_for_home_review" if entry.get("home_pr") else "pending_retry",
                        "pr_url": entry.get("home_pr"),
                        "vault_tracking": "existing_pending_method",
                    }
                    if not entry.get("home_pr"):
                        try:
                            submitted = create_home_pull_request(
                                HOME_REPO, HOME_BRANCH, child_id, existing_pending["record"], HOME_WRITE_TOKEN
                            )
                            mark_pending_home_submission(
                                existing_pending["record"]["id"],
                                submitted["pr_url"],
                                submission_kind="pull_request",
                            )
                            home_submission = {
                                "status": "submitted_for_home_review",
                                "pr_url": submitted["pr_url"],
                                "branch": submitted["branch"],
                                "vault_tracking": "linked_to_home_pull_request",
                                "idempotent_retry": True,
                            }
                        except Exception as exc:
                            home_submission = {
                                "status": "pending_retry",
                                "reason": type(exc).__name__,
                                "vault_tracking": "existing_pending_method",
                            }
                    verification = {
                        "status": "runtime_tests_passed",
                        "authority": "trusted_vorax_vlang_interpreter",
                        "tests_passed": len(existing_pending["record"].get("tests", [])),
                        "method_sha256": method_sha,
                        "home_certification": "pending",
                        "child_self_certification": False,
                        "idempotent_recovery": True,
                    }
                    return {
                        "status": "verified",
                        "method": existing_pending["record"]["id"],
                        "method_sha256": method_sha,
                        "artifact_sha256": None,
                        "verification": verification,
                        "vault": {
                            "status": "already_pending",
                            "path": existing_pending["path"],
                            "sha256": method_sha,
                        },
                        "home_submission": home_submission,
                    }
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

    if cycle_id:
        if not hunter_id:
            raise HTTPException(422, "hunter_id is required with cycle_id")
        try:
            claim = claim_weekly_proposal_digest(method_sha, cycle_id, hunter_id)
        except VaultError as exc:
            raise HTTPException(503, "Runtime weekly proposal deduplication is unavailable") from exc
        if claim.get("duplicate") is True:
            raise HTTPException(409, {
                "status": "duplicate",
                "reason": "method_sha256_already_claimed_in_cycle",
                "method_sha256": method_sha,
                "first_hunter_id": claim.get("first_hunter_id"),
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
        raise HTTPException(503, "Method passed Runtime tests but the Vault pending write failed; no Home proposal PR was opened") from exc

    home_submission = {"status": "not_submitted"}
    try:
        if not HOME_WRITE_TOKEN:
            raise GitHubHomeError("VORAX_HOME_WRITE_TOKEN is not configured")
        submitted = create_home_pull_request(
            HOME_REPO,
            HOME_BRANCH,
            child_id,
            record,
            HOME_WRITE_TOKEN,
        )
        home_submission = {
            "status": "submitted_for_home_review",
            "pr_number": submitted["pr_number"],
            "pr_url": submitted["pr_url"],
            "branch": submitted["branch"],
            "proposal_commit_sha": submitted["proposal_commit_sha"],
            "vault_tracking": "pending",
        }
        try:
            mark_pending_home_submission(
                record["id"],
                submitted["pr_url"],
                submission_kind="pull_request",
            )
            home_submission["vault_tracking"] = "linked_to_home_pull_request"
        except Exception as exc:
            home_submission["vault_tracking"] = "update_failed"
            home_submission["vault_tracking_error"] = type(exc).__name__

        # This read is deliberately against the configured Home base branch,
        # never the proposal branch. The Vault remains pending while the PR is open.
        try:
            merged_index, merged_index_status = _refresh_home_index(force=True)
            if merged_index is None:
                home_submission["index_status"] = merged_index_status.get("status")
            else:
                merged_reconciliation = _reconcile_home_methods(merged_index)
                home_reconciliation = merged_reconciliation
                if record["id"] in merged_reconciliation.get("loaded_method_ids", []):
                    verification["home_certification"] = "confirmed_by_merged_home_index"
                    home_submission["status"] = "merged_and_promoted"
                else:
                    home_submission["status"] = "submitted_for_home_review"
        except Exception as exc:
            # A PR is durable even if the post-submission metadata refresh is unavailable.
            home_submission["reconciliation_error"] = type(exc).__name__
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
        verification=(
            "runtime_tests_passed_home_pr_opened"
            if home_submission.get("pr_number")
            else "runtime_tests_passed_home_submission_pending"
        ),
        method_sha256=method_sha,
        home_pr=home_submission.get("pr_url"),
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
    TEACHER_MONITOR.start()
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
        "teacher_peer": TEACHER_MONITOR.snapshot(),
        "home_method_bodies_loaded": False,
        "vault": vault,
        "required_secrets": required,
        "ready": all(required.values()) and home_index is not None and vault.get("status") == "verified",
        "billable_inference_allowed": os.getenv("VORAX_ALLOW_BILLABLE_INFERENCE", "").strip().casefold() in {"1", "true", "yes"},
        "free_provider_only": os.getenv("VORAX_ALLOW_BILLABLE_INFERENCE", "").strip().casefold() not in {"1", "true", "yes"},
        "weights_downloaded": False,
        "local_model_loaded": False,
    }


@app.get("/vorax/home-index")
def get_home_index(
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    child_id = _require_child(x_child_id, x_child_key)
    index, index_status = _refresh_home_index(force=True)
    if index is None:
        raise HTTPException(503, "Home metadata-only index is unavailable")
    _log(
        "home_metadata_index_read",
        child_id=child_id,
        x="Home metadata index",
        method=None,
        verification=index_status.get("status", "metadata_only"),
        index_methods=index["methods_total"],
    )
    return {
        **index,
        "method_bodies_included": False,
        "index_source_status": index_status,
    }


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
    cycle_id = body.get("cycle_id")
    hunter_id = body.get("hunter_id")
    if bool(cycle_id) != bool(hunter_id):
        raise HTTPException(422, "cycle_id and hunter_id must be supplied together")
    if cycle_id:
        try:
            validate_cycle_id(cycle_id)
            manifest = read_weekly_cycle_manifest(cycle_id)
        except (WeeklyCycleError, VaultError) as exc:
            raise HTTPException(503, "Weekly cycle manifest is unavailable or invalid") from exc
        if not manifest:
            raise HTTPException(409, "Weekly cycle has not been started by Runtime")
        assignment = next(
            (item for item in manifest.get("hunter_assignments", []) if item.get("hunter_id") == hunter_id),
            None,
        )
        if not assignment or assignment.get("status") not in {"queued", "running"}:
            raise HTTPException(409, "Hunter is not active in this weekly cycle")
        if hunter_id != "hunter-1":
            raise HTTPException(
                403,
                "Vision/Audio and Video/Tools hunters are discovery-only until Runtime supports verified multimodal artifacts",
            )
        if body.get("attempt") != assignment.get("attempt", 0):
            raise HTTPException(409, "Hunter attempt is required and must match the current cycle assignment")
        if any(
            str(step.get("op", "")).casefold() == "call_tool"
            for step in method.get("steps", [])
            if isinstance(step, dict)
        ):
            raise HTTPException(
                422,
                "Weekly CALL TOOL acquisition requires real remote artifact evidence; contract-only tests are insufficient",
            )
        if hunter_id == "hunter-1":
            task = manifest.get("curriculum_task")
            if not isinstance(task, dict):
                raise HTTPException(409, "Runtime has no uncovered text/doc curriculum task for this cycle")
            try:
                validate_candidate_for_curriculum(method, task)
            except WeeklyCycleError as exc:
                raise HTTPException(422, str(exc)) from exc
    return _verify_and_submit(
        method, child_id, teacher_id, x_value, cycle_id=cycle_id, hunter_id=hunter_id
    )


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



def _finalize_weekly_cycle_report(manifest: dict) -> dict:
    """Persist one aggregate report and retry its Home PR without overwriting prior reports."""
    cycle_id = manifest.get("cycle_id")
    if manifest.get("status") not in {"complete", "failed"}:
        return {"status": "not_terminal"}
    try:
        final_report = read_weekly_report(cycle_id)
        if final_report is None:
            reports = []
            for assignment in manifest.get("hunter_assignments", []):
                attempt = int(assignment.get("attempt", 0))
                hunter_report = read_weekly_hunter_report(
                    cycle_id,
                    assignment.get("hunter_id"),
                    attempt,
                )
                if hunter_report is None:
                    return {
                        "status": "incomplete",
                        "reason": "missing_hunter_report",
                        "hunter_id": assignment.get("hunter_id"),
                        "attempt": attempt,
                    }
                reports.append(hunter_report)
            final_report = aggregate_hunter_reports(manifest, reports)
            final_report["status"] = manifest["status"]
            # Once this immutable report exists, later retries reuse these exact bytes.
            vault_receipt = write_weekly_report(final_report)
        else:
            vault_receipt = {
                "status": "already_stored",
                "path": f"reports/weekly/{cycle_id}.json",
            }
    except VaultError as exc:
        raise HTTPException(503, "Runtime could not finalize the weekly report") from exc

    submission = manifest.get("home_report_submission") or {}
    if submission.get("status") not in {
        "submitted_for_home_review",
        "already_in_home",
    }:
        try:
            submission = create_home_report_pull_request(
                HOME_REPO,
                HOME_BRANCH,
                final_report,
                HOME_WRITE_TOKEN,
            )
        except Exception as exc:
            submission = {
                "status": "pending_retry",
                "reason": type(exc).__name__,
            }
        manifest["home_report_submission"] = submission
    return {
        "status": "finalized",
        "report": final_report,
        "vault": vault_receipt,
        "home_report": submission,
    }


@app.post("/vorax/cycle/start")
def start_weekly_cycle(
    body: dict,
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    child_id = _require_child(x_child_id, x_child_key)
    cycle_id = body.get("cycle_id")
    try:
        validate_cycle_id(cycle_id)
    except WeeklyCycleError as exc:
        raise HTTPException(422, str(exc)) from exc

    try:
        existing = read_weekly_cycle_manifest(cycle_id)
        peer = TEACHER_MONITOR.check_once()
        if existing:
            now = datetime.now(timezone.utc)
            updated = existing
            if not updated.get("home_issue_url") and body.get("home_issue_url"):
                updated["home_issue_url"] = body.get("home_issue_url")
            for assignment in list(updated.get("hunter_assignments", [])):
                if assignment.get("status") != "running":
                    continue
                deadline = assignment.get("deadline_at")
                try:
                    deadline_at = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
                except ValueError:
                    deadline_at = now - timedelta(seconds=1)
                if deadline_at <= now:
                    current_report = read_weekly_hunter_report(
                        cycle_id, assignment["hunter_id"], int(assignment.get("attempt", 0))
                    )
                    if current_report:
                        timed_out_state = current_report.get("status", "completed")
                        updated = update_hunter_assignment(
                            updated,
                            assignment["hunter_id"],
                            status="completed" if timed_out_state == "completed" else "failed",
                            report=current_report,
                            now=now,
                        )
                        continue
                    updated = update_hunter_assignment(
                        updated,
                        assignment["hunter_id"],
                        status="timed_out",
                        reason="hunter exceeded its two-hour execution deadline",
                        now=now,
                    )
                    timeout_report = {
                        "status": "failed",
                        "cycle_id": cycle_id,
                        "hunter_id": assignment["hunter_id"],
                        "discovery_slice": assignment["discovery_slice"],
                        "attempt": int(assignment.get("attempt", 0)),
                        "discovered_count": 0,
                        "understood_count": 0,
                        "acquired_count": 0,
                        "verified_count": 0,
                        "added_count": 0,
                        "rejected_count": 1,
                        "rejected": [{
                            "reason": "hunter_timeout",
                            "detail": "Runtime observed that the two-hour hunter deadline expired",
                        }],
                        "missing_X_list": [],
                        "next_priority": [],
                        "weights_downloaded": False,
                        "local_model_loaded": False,
                        "billable_inference_allowed": False,
                    }
                    write_weekly_hunter_report(timeout_report)

            # Retry any previously Runtime-verified method proposal whose Home PR
            # could not be created during a GitHub outage. Runtime remains the sole writer.
            home_proposal_retries = []
            try:
                for proposal in pending_home_proposals(limit=5):
                    try:
                        home_receipt = create_home_pull_request(
                            HOME_REPO,
                            HOME_BRANCH,
                            proposal["child_id"],
                            proposal["record"],
                            HOME_WRITE_TOKEN,
                        )
                        mark_pending_home_submission(
                            proposal["record"]["id"],
                            home_receipt["pr_url"],
                            submission_kind="pull_request",
                        )
                        home_proposal_retries.append({
                            "method_id": proposal["record"]["id"],
                            "status": "submitted_for_home_review",
                            "pr_url": home_receipt["pr_url"],
                        })
                    except Exception as proposal_exc:
                        home_proposal_retries.append({
                            "method_id": proposal["record"]["id"],
                            "status": "pending_retry",
                            "reason": type(proposal_exc).__name__,
                        })
            except VaultError as proposal_exc:
                home_proposal_retries.append({
                    "status": "vault_unavailable",
                    "reason": type(proposal_exc).__name__,
                })

            # Reconcile cycle-level state from the persisted hunter assignments,
            # including workers that completed while the cycle was paused.
            hunter_states = [
                item.get("status")
                for item in updated.get("hunter_assignments", [])
            ]
            if hunter_states and all(state == "completed" for state in hunter_states):
                updated["status"] = "complete"
                updated["retry_after"] = None
            elif hunter_states and all(
                state in {"completed", "failed", "timed_out"}
                for state in hunter_states
            ):
                if int(updated.get("retry_count", 0)) < 1:
                    if updated.get("status") != "retry_scheduled":
                        updated["retry_after"] = timestamp(now + timedelta(hours=24))
                    updated["status"] = "retry_scheduled"
                else:
                    updated["status"] = "failed"
                    updated["retry_after"] = None
                    updated["failure_reason"] = "Hunter mesh remained incomplete after one retry"

            if updated.get("status") == "paused_teacher" and peer.get("live") is True:
                updated["status"] = "discovering"
                updated.pop("pause_reason", None)
            elif updated.get("status") == "discovering" and peer.get("live") is not True:
                updated["status"] = "paused_teacher"
                updated["pause_reason"] = "teacher_gateway_unavailable"

            updated = prepare_cycle_retry(updated, now=now)
            # A due retry must remain paused while the Teacher gateway is down.
            if updated.get("status") == "discovering" and peer.get("live") is not True:
                updated["status"] = "paused_teacher"
                updated["pause_reason"] = "teacher_gateway_unavailable"

            # A terminal cycle always has an aggregate report before it is complete.
            finalization = _finalize_weekly_cycle_report(updated)
            if updated != existing:
                write_weekly_cycle_manifest(updated)
            return {
                "status": updated.get("status"),
                "manifest": updated,
                "teacher_peer": peer,
                "home_proposal_retries": home_proposal_retries,
                "weekly_report": finalization,
                "reused": True,
            }

        home_index, index_status = _refresh_home_index(force=True)
        if home_index is None:
            raise HTTPException(
                503,
                "Home metadata-only index is unavailable; Runtime will not begin a duplicate-blind cycle",
            )
        try:
            pending_methods = pending_method_coverage()
            pending_ids = {
                item.get("id")
                for item in pending_methods
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
        except VaultError as exc:
            raise HTTPException(503, "Runtime could not inspect pending method coverage for curriculum de-duplication") from exc
        manifest = build_cycle_manifest(
            cycle_id,
            hunter_count=body.get("hunter_count", 3),
            home_issue_url=body.get("home_issue_url"),
            home_index=home_index,
            pending_method_ids=pending_ids,
            pending_methods=pending_methods,
        )
        manifest["home_index_status"] = index_status.get("status")
        manifest["home_index_stale"] = bool(index_status.get("stale"))
        if peer.get("live") is not True:
            manifest["status"] = "paused_teacher"
            manifest["pause_reason"] = "teacher_gateway_unavailable"
        receipt = write_weekly_cycle_manifest(manifest)
    except WeeklyCycleError as exc:
        raise HTTPException(422, str(exc)) from exc
    except VaultError as exc:
        raise HTTPException(503, "Runtime could not persist the weekly cycle manifest") from exc

    _log(
        "weekly_cycle_started",
        child_id=child_id,
        x="weekly knowledge-growth cycle",
        method=cycle_id,
        verification=manifest["status"],
        hunter_count=len(manifest["hunter_assignments"]),
        curriculum_task=(manifest.get("curriculum_task") or {}).get("id"),
        home_index_status=index_status.get("status"),
        weights_downloaded=False,
    )
    return {"status": manifest["status"], "manifest": manifest, "vault": receipt, "teacher_peer": peer, "reused": False}

@app.get("/vorax/cycle/pending")
def list_pending_hunter_reports(
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    """List durable reports, old-cycle reconciliation needs, and queued assignments."""
    _require_child(x_child_id, x_child_key)
    pending_reports = []
    queued_hunters = []
    reconcile_cycles = []
    try:
        manifests = list_weekly_cycle_manifests(limit=1000)
        for manifest in manifests:
            cycle_id = manifest["cycle_id"]
            updated = manifest
            changed = False
            if updated.get("status") in {"discovering", "paused_teacher", "retry_scheduled"}:
                reconcile_cycles.append({
                    "cycle_id": cycle_id,
                    "status": updated.get("status"),
                })
            for assignment in list(updated.get("hunter_assignments", [])):
                hunter_id = assignment.get("hunter_id")
                attempt = int(assignment.get("attempt", 0))
                if assignment.get("status") != "running":
                    continue
                report = read_weekly_hunter_report(cycle_id, hunter_id, attempt)
                if report is not None:
                    updated = update_hunter_assignment(
                        updated,
                        hunter_id,
                        status=report.get("status", "completed"),
                        report=report,
                        reason=report.get("failure_reason"),
                    )
                    changed = True
                else:
                    pending_reports.append({
                        "cycle_id": cycle_id,
                        "hunter_id": hunter_id,
                        "discovery_slice": assignment.get("discovery_slice"),
                        "attempt": attempt,
                        "report_path": assignment.get("report_path"),
                        "status": assignment.get("status"),
                    })
            if changed:
                write_weekly_cycle_manifest(updated)
                _finalize_weekly_cycle_report(updated)
                write_weekly_cycle_manifest(updated)

            if updated.get("status") == "discovering":
                for assignment in updated.get("hunter_assignments", []):
                    if assignment.get("status") != "queued":
                        continue
                    queued_hunters.append({
                        "cycle_id": cycle_id,
                        "hunter_id": assignment.get("hunter_id"),
                        "discovery_slice": assignment.get("discovery_slice"),
                        "attempt": int(assignment.get("attempt", 0)),
                        "report_path": assignment.get("report_path"),
                        "status": "queued",
                        "retry_count": int(updated.get("retry_count", 0)),
                        "cycle_created_at": updated.get("created_at"),
                        "curriculum_task": updated.get("curriculum_task") if assignment.get("hunter_id") == "hunter-1" else None,
                    })
    except (VaultError, WeeklyCycleError) as exc:
        raise HTTPException(503, "Runtime could not list pending weekly hunter work") from exc
    return {
        "pending_reports": pending_reports[:300],
        "pending_count": len(pending_reports),
        "queued_hunters": queued_hunters[:300],
        "queued_count": len(queued_hunters),
        "reconcile_cycles": reconcile_cycles[:1000],
        "reconcile_count": len(reconcile_cycles),
        "vault_source": "Runtime_owned_weekly_manifests",
        "children_have_vault_access": False,
    }



@app.get("/vorax/cycle/status")
def get_weekly_cycle_status(
    cycle_id: str,
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    _require_child(x_child_id, x_child_key)
    try:
        validate_cycle_id(cycle_id)
        manifest = read_weekly_cycle_manifest(cycle_id)
    except (WeeklyCycleError, VaultError) as exc:
        raise HTTPException(503, "Weekly cycle status could not be read") from exc
    if not manifest:
        raise HTTPException(404, "Weekly cycle was not found")
    return {
        "status": manifest.get("status"),
        "manifest": manifest,
        "teacher_peer": TEACHER_MONITOR.snapshot(),
    }


@app.post("/vorax/cycle/status")
def update_weekly_hunter_status(
    body: dict,
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    child_id = _require_child(x_child_id, x_child_key)
    cycle_id = body.get("cycle_id")
    hunter_id = body.get("hunter_id")
    state = body.get("status")
    try:
        validate_cycle_id(cycle_id)
        manifest = read_weekly_cycle_manifest(cycle_id)
        if not manifest:
            raise HTTPException(404, "Weekly cycle was not found")
        assignment = next(
            (item for item in manifest.get("hunter_assignments", []) if item.get("hunter_id") == hunter_id),
            None,
        )
        if not assignment:
            raise HTTPException(403, "Hunter is not assigned to this cycle")
        if body.get("attempt", assignment.get("attempt", 0)) != assignment.get("attempt", 0):
            raise HTTPException(409, "Hunter attempt does not match the current cycle assignment")
        if state == "completed":
            existing_success_report = read_weekly_hunter_report(
                cycle_id, hunter_id, int(assignment.get("attempt", 0))
            )
            if not existing_success_report or existing_success_report.get("status") != "completed":
                raise HTTPException(409, "A successful hunter report is required before marking a hunter completed")
        if state in {"failed", "timed_out"}:
            existing_report = read_weekly_hunter_report(
                cycle_id, hunter_id, int(assignment.get("attempt", 0))
            )
            if not existing_report:
                failure_report = {
                    "status": "failed",
                    "cycle_id": cycle_id,
                    "hunter_id": hunter_id,
                    "discovery_slice": assignment["discovery_slice"],
                    "attempt": int(assignment.get("attempt", 0)),
                    "discovered_count": 0,
                    "understood_count": 0,
                    "acquired_count": 0,
                    "verified_count": 0,
                    "added_count": 0,
                    "rejected_count": 1,
                    "rejected": [{
                        "reason": str(body.get("reason") or state)[:120],
                        "detail": "Runtime recorded a failed hunter dispatch; no success was inferred",
                    }],
                    "missing_X_list": [],
                    "next_priority": [],
                    "weights_downloaded": False,
                    "local_model_loaded": False,
                    "billable_inference_allowed": False,
                }
                write_weekly_hunter_report(failure_report)
        updated = update_hunter_assignment(
            manifest,
            hunter_id,
            status=state,
            reason=body.get("reason"),
        )
        finalization = _finalize_weekly_cycle_report(updated)
        receipt = write_weekly_cycle_manifest(updated)
    except WeeklyCycleError as exc:
        raise HTTPException(422, str(exc)) from exc
    except VaultError as exc:
        raise HTTPException(503, "Runtime could not persist hunter status") from exc
    _log(
        "weekly_hunter_status",
        child_id=child_id,
        x="weekly hunter status",
        method=hunter_id,
        verification=state,
        cycle_id=cycle_id,
    )
    return {
        "status": updated.get("status"),
        "manifest": updated,
        "vault": receipt,
        "weekly_report": finalization,
    }

def _replay_cached_hunter_proposals(
    child_id: str,
    cycle_id: str,
    hunter_id: str,
    assignment: dict,
    report: dict,
) -> dict:
    """Replay cached Teacher-attested candidates through Runtime after a network outage."""
    cached = report.get("cached_proposals", [])
    if not isinstance(cached, list):
        raise HTTPException(422, "cached_proposals must be a list")
    if not cached:
        return {"replayed": 0, "verified": 0, "rejected": 0}
    if hunter_id != "hunter-1":
        raise HTTPException(
            403,
            "Vision/Audio and Video/Tools hunters cannot submit methods without a verified multimodal artifact path",
        )

    replayed = 0
    verified_count = 0
    rejected = list(report.get("rejected", [])) if isinstance(report.get("rejected"), list) else []
    added_count = 0
    outcomes = []
    for proposal in cached[:5]:
        if not isinstance(proposal, dict):
            rejected.append({"reason": "invalid_cached_proposal"})
            continue
        method = proposal.get("method")
        teacher_response = proposal.get("teacher_response")
        teacher_attestation = proposal.get("teacher_attestation")
        teacher_model_id = proposal.get("teacher_model_id")
        if (
            not isinstance(method, dict)
            or not isinstance(teacher_response, str)
            or not isinstance(teacher_model_id, str)
        ):
            rejected.append({"method_id": (method or {}).get("id") if isinstance(method, dict) else None,
                             "reason": "cached_teacher_evidence_incomplete"})
            continue
        try:
            _validate_teacher_candidate(
                teacher_response,
                teacher_attestation,
                method,
                teacher_model_id,
                allow_expired=True,
            )
            if any(
                str(step.get("op", "")).casefold() == "call_tool"
                for step in method.get("steps", [])
                if isinstance(step, dict)
            ):
                raise HTTPException(422, "Weekly CALL TOOL candidate has no independently verified media artifact")
            if hunter_id == "hunter-1":
                task = None
                # The cycle task is read from the persisted manifest, never trusted from the child report.
                try:
                    cycle_manifest = read_weekly_cycle_manifest(cycle_id)
                except VaultError as exc:
                    raise HTTPException(503, "Runtime could not read the weekly curriculum task") from exc
                task = cycle_manifest.get("curriculum_task") if cycle_manifest else None
                if not isinstance(task, dict):
                    raise HTTPException(409, "No text/doc curriculum task is assigned for this cycle")
                validate_candidate_for_curriculum(method, task)
            result = _verify_and_submit(
                method,
                child_id,
                teacher_model_id,
                str(method.get("task") or method.get("id") or "weekly method recovery"),
                cycle_id=cycle_id,
                hunter_id=hunter_id,
            )
            replayed += 1
            if result.get("status") == "verified":
                verified_count += 1
                if (result.get("home_submission") or {}).get("status") == "merged_and_promoted":
                    added_count += 1
                outcomes.append({
                    "method_id": result.get("method"),
                    "method_sha256": result.get("method_sha256"),
                    "status": "verified",
                    "home_submission": (result.get("home_submission") or {}).get("status"),
                })
        except HTTPException as exc:
            if exc.status_code >= 500:
                raise
            detail = exc.detail
            reason = detail.get("reason") if isinstance(detail, dict) else str(detail)
            rejected.append({
                "method_id": method.get("id"),
                "reason": str(reason or "runtime_rejected_cached_proposal")[:200],
            })
            outcomes.append({
                "method_id": method.get("id"),
                "status": "rejected",
                "reason": str(reason or "runtime_rejected_cached_proposal")[:200],
            })

    # One hunter currently proposes at most one curriculum method per cycle.
    # A report can already contain a successful Runtime receipt from before its
    # delivery connection dropped; max() preserves that evidence without double-counting.
    report["acquired_count"] = max(
        0, int(report.get("acquired_count", 0) or 0), verified_count
    )
    report["verified_count"] = max(
        0, int(report.get("verified_count", 0) or 0), verified_count
    )
    report["added_count"] = max(
        0, int(report.get("added_count", 0) or 0), added_count
    )
    report["rejected"] = rejected[:100]
    report["rejected_count"] = len(report["rejected"])
    report["cached_proposal_replay"] = {
        "replayed": replayed,
        "verified": verified_count,
        "rejected": len(outcomes) - verified_count,
        "outcomes": outcomes,
        "authority": "trusted_runtime_teacher_attestation_and_vlang_tests",
    }
    if verified_count or replayed:
        report["status"] = "completed"
        if report.get("failure"):
            report["recovered_failure"] = report["failure"]
    return {
        "replayed": replayed,
        "verified": verified_count,
        "rejected": len(outcomes) - verified_count,
    }



@app.post("/vorax/cycle/hunter-report")
def receive_weekly_hunter_report(
    body: dict,
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    child_id = _require_child(x_child_id, x_child_key)
    cycle_id = body.get("cycle_id")
    hunter_id = body.get("hunter_id")
    attempt = body.get("attempt", 0)
    raw_report = body.get("report")
    try:
        validate_cycle_id(cycle_id)
        manifest = read_weekly_cycle_manifest(cycle_id)
        if not manifest:
            raise HTTPException(404, "Weekly cycle was not found")
        assignment = next(
            (item for item in manifest.get("hunter_assignments", []) if item.get("hunter_id") == hunter_id),
            None,
        )
        if not assignment:
            raise HTTPException(403, "Hunter is not assigned to this cycle")
        if attempt != assignment.get("attempt", 0):
            raise HTTPException(409, "Stale hunter report attempt; it will not replace current evidence")
        if not isinstance(raw_report, dict):
            raise HTTPException(422, "Hunter report must be a JSON object")
        source_report_sha = hashlib.sha256(
            json.dumps(raw_report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        report = validate_hunter_report(
            raw_report,
            cycle_id=cycle_id,
            hunter_id=hunter_id,
            discovery_slice=assignment["discovery_slice"],
            attempt=attempt,
        )
        if report.get("status") == "completed" or report.get("cached_proposals"):
            _replay_cached_hunter_proposals(
                child_id,
                cycle_id,
                hunter_id,
                assignment,
                report,
            )
        report["source_report_sha256"] = source_report_sha
        existing_report = read_weekly_hunter_report(cycle_id, hunter_id, attempt)
        if existing_report:
            if existing_report.get("source_report_sha256") != source_report_sha:
                raise HTTPException(409, "A different report already exists for this hunter attempt")
            report = existing_report
            report_receipt = {
                "status": "already_stored",
                "path": f"reports/weekly/hunters/{cycle_id}/{hunter_id}-attempt-{attempt}.json",
            }
        else:
            report_receipt = write_weekly_hunter_report(report)
        if assignment.get("status") in {"completed", "failed"}:
            return {"status": manifest.get("status"), "duplicate_report": True, "vault": report_receipt}

        updated = update_hunter_assignment(
            manifest,
            hunter_id,
            status=report["status"],
            report=report,
            reason=report.get("failure_reason"),
        )
        manifest_receipt = write_weekly_cycle_manifest(updated)
    except WeeklyCycleError as exc:
        raise HTTPException(422, str(exc)) from exc
    except VaultError as exc:
        raise HTTPException(503, "Runtime could not store the weekly hunter report") from exc

    finalization = _finalize_weekly_cycle_report(updated)
    manifest_receipt = write_weekly_cycle_manifest(updated)


    _log(
        "weekly_hunter_report_received",
        child_id=child_id,
        x="weekly hunter report",
        method=hunter_id,
        verification=updated.get("status"),
        cycle_id=cycle_id,
        discovered_count=report.get("discovered_count", 0),
        verified_count=report.get("verified_count", 0),
    )
    return {
        "status": updated.get("status"),
        "manifest": updated,
        "hunter_report": report_receipt,
        "manifest_receipt": manifest_receipt,
        "weekly_report": finalization.get("vault"),
        "home_report": finalization.get("home_report"),
        "finalization": finalization,
    }
