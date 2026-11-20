"""VORAX Teacher Service: metadata discovery and remote, weight-free model calls."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
import concurrent.futures
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

LOGGER = logging.getLogger("vorax.teacher-service")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
HF_MODELS_API = "https://huggingface.co/api/models"
HF_CHAT_API = "https://router.huggingface.co/v1/chat/completions"
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)?$")
MAX_PROMPT_CHARS = 12_000

app = FastAPI(
    title="VORAX Teacher Service",
    version="1.0.0",
    description="Remote Hugging Face Inference Providers gateway; no local model weights.",
)


class TeacherAsk(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    model_id: str = Field(min_length=3, max_length=220)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(event: str, **fields) -> None:
    LOGGER.info(json.dumps({"event": event, "timestamp": _utc_now(), **fields}, ensure_ascii=False))


def _authenticate_child(child_id: str | None, child_key: str | None) -> str:
    expected_id = os.getenv("VORAX_CHILD_ID", "kaggle-child").strip()
    expected_key = os.getenv("VORAX_KAGGLE_CHILD_API_KEY", "").strip()
    if not expected_key:
        raise HTTPException(503, "Teacher child authentication is not configured")
    if not child_id or child_id != expected_id or not child_key or not hmac.compare_digest(child_key, expected_key):
        raise HTTPException(403, "Authenticated VORAX Learning Connector required")
    return child_id


def _http_json(url: str, *, token: str = "", body: dict | None = None, timeout: int = 25):
    headers = {"Accept": "application/json", "User-Agent": "VORAX-Teacher-Service/1.0"}
    data = None
    method = "GET"
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        method = "POST"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise HTTPException(502, "Hugging Face response exceeded the metadata/response limit")
            return json.loads(raw.decode("utf-8")), dict(response.headers)
    except urllib.error.HTTPError as exc:
        if body is None:
            raise HTTPException(502, f"Hugging Face metadata API returned HTTP {exc.code}") from exc
        # Do not echo upstream response bodies; they can contain sensitive provider diagnostics.
        if exc.code in {401, 403}:
            raise HTTPException(502, "Hugging Face rejected the configured access token or permissions") from exc
        raise HTTPException(502, f"Remote Inference Provider returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "Hugging Face request failed: " + type(exc).__name__) from exc


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "teacher",
        "remote_inference_configured": bool(os.getenv("HF_TOKEN")),
        "metadata_token_configured": bool(os.getenv("HF_READ_TOKEN")),
        "attestation_configured": len(os.getenv("VORAX_TEACHER_ATTESTATION_KEY", "").strip()) >= 32,
        "weights_downloaded": False,
        "local_model_loaded": False,
        "kaggle_t4_used": False,
    }


def _next_cursor(headers: dict) -> str | None:
    link = headers.get("Link") or headers.get("link") or ""
    match = re.search(r'<[^>]*[?&]cursor=([^&>]+)[^>]*>;\s*rel="next"', link)
    return urllib.parse.unquote(match.group(1)) if match else None


def _metadata_item(item: dict, source_type: str) -> dict | None:
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id:
        return None
    card = item.get("cardData") if isinstance(item.get("cardData"), dict) else {}
    description = (
        card.get("description")
        or card.get("summary")
        or item.get("description")
        or ""
    )
    return {
        "id": item_id,
        "source_type": source_type,
        "tags": [str(tag)[:100] for tag in (item.get("tags") or []) if isinstance(tag, (str, int))][:40],
        "description": str(description)[:1000],
        "downloads": int(item.get("downloads") or 0),
        "likes": int(item.get("likes") or 0),
        "pipeline_tag": item.get("pipeline_tag"),
        "library_name": item.get("library_name"),
        "sdk": item.get("sdk"),
    }


def _discover_kind(kind: str, query: str, limit: int, cursor: str | None = None):
    if kind not in {"models", "spaces", "datasets"}:
        raise HTTPException(422, "source must be models, spaces, datasets, or all")
    params = {"limit": str(limit), "sort": "downloads", "direction": "-1"}
    if query.strip():
        params["search"] = query.strip()
    if cursor:
        params["cursor"] = cursor
    url = f"https://huggingface.co/api/{kind}?" + urllib.parse.urlencode(params)
    payload, headers = _http_json(
        url,
        token=os.getenv("HF_READ_TOKEN", "").strip(),
        timeout=25,
    )
    if not isinstance(payload, list):
        raise HTTPException(502, f"Hugging Face {kind} discovery returned an unexpected response")
    rows = []
    for item in payload[:limit]:
        if isinstance(item, dict):
            row = _metadata_item(item, kind[:-1] if kind.endswith("s") else kind)
            if row:
                rows.append(row)
    return rows, _next_cursor(headers)


@app.get("/teacher/discover")
def discover(
    query: str = Query(default="", max_length=160),
    limit: int = Query(default=20, ge=1, le=100),
    source: str = Query(default="all", pattern="^(models|spaces|datasets|all)$"),
    cursor: str | None = Query(default=None, max_length=2000),
):
    """Discover Hub model/service/data metadata only. No file, config or weight is downloaded."""
    kinds = ("models", "spaces", "datasets") if source == "all" else (source,)
    found = {}
    cursors = {}
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            pending = {
                kind: pool.submit(_discover_kind, kind, query, limit, cursor if kind == "models" else None)
                for kind in kinds
            }
            for kind, future in pending.items():
                found[kind], cursors[kind] = future.result()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, "Hugging Face metadata discovery failed: " + type(exc).__name__) from exc

    models = found.get("models", [])
    resources = [item for kind in kinds for item in found.get(kind, [])]
    return {
        "status": "metadata_only",
        "query": query,
        "sources_searched": list(kinds),
        "models": models,
        "resources": resources,
        "models_found": len(models),
        "resources_found": len(resources),
        "next_cursor": cursors.get(source) if source != "all" else cursors.get("models"),
        "next_cursors": cursors,
        "weights_downloaded": False,
        "local_model_loaded": False,
    }

def _attest_response(model_id: str, provider: str, content: str, request_id: str | None) -> dict:
    """Sign remote Teacher provenance for Runtime verification; Kaggle never receives the key."""
    secret = os.getenv("VORAX_TEACHER_ATTESTATION_KEY", "").strip()
    if len(secret) < 32:
        raise HTTPException(503, "Teacher attestation key is not configured")
    payload = {
        "model_id": model_id,
        "provider_used": provider,
        "remote_inference": True,
        "kaggle_t4_used": False,
        "weights_downloaded": False,
        "response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "request_id": request_id,
        "issued_at": int(time.time()),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    signature = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"algorithm": "hmac-sha256", "payload": payload, "signature": signature}


@app.post("/teacher/ask")
def ask(
    body: TeacherAsk,
    x_child_id: str | None = Header(default=None, alias="x-child-id"),
    x_child_key: str | None = Header(default=None, alias="x-child-key"),
):
    child_id = _authenticate_child(x_child_id, x_child_key)
    model_id = body.model_id.strip()
    if not MODEL_ID_RE.fullmatch(model_id):
        raise HTTPException(422, "model_id must be a Hugging Face model ID with optional provider suffix")
    token = os.getenv("HF_TOKEN", "").strip()
    if not token:
        raise HTTPException(503, "HF_TOKEN is not configured for remote Inference Providers")
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "You are a VORAX capability teacher. Be factual, provide a method that can be represented as bounded, testable V-Lang data, and never claim that tests or artifacts were executed when they were not."},
            {"role": "user", "content": body.prompt},
        ],
        "temperature": 0,
        "max_tokens": 1600,
        "stream": False,
    }
    response, headers = _http_json(HF_CHAT_API, token=token, body=payload, timeout=90)
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(502, "Remote Inference Provider returned no assistant content") from exc
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(502, "Remote Inference Provider returned empty assistant content")
    provider = headers.get("x-inference-provider") or headers.get("X-Inference-Provider") or "unknown"
    request_id = headers.get("x-request-id") or headers.get("X-Request-Id") or None
    attestation = _attest_response(model_id, provider, content, request_id)
    _log(
        "teacher_remote_inference",
        child_id=child_id,
        model_id=model_id,
        provider_used=provider,
        remote_inference=True,
        kaggle_t4_used=False,
        local_model_loaded=False,
        weights_downloaded=False,
        request_id=request_id,
        attestation="signed",
    )
    return {
        "status": "teacher_response",
        "model_id": model_id,
        "provider_used": provider,
        "remote_inference": True,
        "kaggle_t4_used": False,
        "weights_downloaded": False,
        "response": content,
        "request_id": request_id,
        "attestation": attestation,
    }
