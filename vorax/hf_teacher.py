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
HF_ROUTER_MODELS_API = "https://router.huggingface.co/v1/models"
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
            if exc.code == 404:
                raise HTTPException(404, "Hugging Face metadata item was not found") from exc
            if exc.code == 429:
                raise HTTPException(503, "Hugging Face metadata API rate limit reached") from exc
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
        "billable_inference_allowed": _billable_inference_allowed(),
        "free_provider_only": not _billable_inference_allowed(),
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
    mapping = item.get("inferenceProviderMapping")
    providers = []
    if isinstance(mapping, dict):
        for provider, details in sorted(mapping.items()):
            if not isinstance(details, dict):
                continue
            providers.append({
                "provider": str(provider),
                "status": str(details.get("status") or ""),
                "provider_model_id": str(details.get("providerId") or ""),
                "task": str(details.get("task") or ""),
            })
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
        "providers": providers,
    }
def _discover_kind(kind: str, query: str, limit: int, cursor: str | None = None):
    if kind not in {"models", "spaces", "datasets"}:
        raise HTTPException(422, "source must be models, spaces, datasets, or all")
    params = [("limit", str(limit))]
    if query.strip():
        params.append(("search", query.strip()))
    if cursor:
        params.append(("cursor", cursor))
    if kind == "models":
        # Search all models exposed by at least one remote Inference Provider.
        params.extend([
            ("inference_provider", "all"),
            ("sort", "downloads"),
            ("direction", "-1"),
            ("expand[]", "tags"),
            ("expand[]", "downloads"),
            ("expand[]", "likes"),
            ("expand[]", "pipeline_tag"),
            ("expand[]", "library_name"),
            ("expand[]", "inferenceProviderMapping"),
        ])
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


def _discover_router_models(
    query: str,
    limit: int,
    *,
    model_ids: list[str] | None = None,
) -> list[dict]:
    """Look up bounded, explicit chat model IDs; never download the global router catalogue."""
    if model_ids is None:
        raise HTTPException(422, "Router metadata lookup requires explicit model IDs")
    token = os.getenv("HF_TOKEN", "").strip()
    if not token and model_ids:
        raise HTTPException(503, "HF_TOKEN is required for the Inference Providers model catalogue")

    selected_ids = []
    for model_id in model_ids:
        if (
            isinstance(model_id, str)
            and MODEL_ID_RE.fullmatch(model_id)
            and ":" not in model_id
            and model_id not in selected_ids
        ):
            selected_ids.append(model_id)
        if len(selected_ids) >= min(max(int(limit), 0), 20):
            break
    if not selected_ids:
        return []

    def lookup(model_id: str) -> dict | None:
        model_path = urllib.parse.quote(model_id, safe="/")
        payload, _ = _http_json(
            f"{HF_ROUTER_MODELS_API}/{model_path}",
            token=token,
            timeout=25,
        )
        if not isinstance(payload, dict) or payload.get("id") != model_id:
            raise HTTPException(502, "Hugging Face router returned invalid per-model metadata")
        entries = payload.get("providers")
        if not isinstance(entries, list):
            raise HTTPException(502, "Hugging Face router model metadata omitted providers")

        provider_rows = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            provider = str(entry.get("provider") or "").strip()
            if not provider:
                continue
            provider_rows.append({
                "provider": provider,
                "status": str(entry.get("status") or ""),
                "provider_model_id": model_id,
                "task": "conversational",
                "context_length": entry.get("context_length"),
                "pricing": entry.get("pricing"),
                "first_token_latency_ms": entry.get("first_token_latency_ms"),
                "throughput": entry.get("throughput"),
                "supports_tools": entry.get("supports_tools"),
                "supports_structured_output": entry.get("supports_structured_output"),
                "is_free": entry.get("is_free"),
            })
        return {
            "id": model_id,
            "source_type": "router_chat_model",
            "owned_by": str(payload.get("owned_by") or ""),
            "tags": ["conversational", "remote-inference"],
            "description": "Listed by the Hugging Face Inference Providers router",
            "pipeline_tag": "conversational",
            "library_name": None,
            "sdk": None,
            "providers": provider_rows,
        }

    # A bounded worker pool keeps metadata discovery responsive and avoids
    # requesting the multi-megabyte global /v1/models catalogue.
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(selected_ids))) as pool:
        futures = [pool.submit(lookup, model_id) for model_id in selected_ids]
        result = []
        for future in futures:
            try:
                item = future.result()
            except HTTPException as exc:
                # A model can be present in Hub metadata but absent from the
                # chat-completions router (for example, image-only models).
                if exc.status_code == 404:
                    continue
                raise
            if item is not None:
                result.append(item)
    return result


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
                kind: pool.submit(
                    _discover_kind,
                    kind,
                    query,
                    limit,
                    cursor if source == kind else None,
                )
                for kind in kinds
            }
            for kind, future in pending.items():
                found[kind], cursors[kind] = future.result()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, "Hugging Face metadata discovery failed: " + type(exc).__name__) from exc

    models = list(found.get("models", []))
    resources = [item for kind in kinds for item in found.get(kind, [])]
    # Hub model metadata supplies the bounded candidate IDs. Enrich only
    # models advertising a conversational provider mapping, including on
    # later cursor pages, instead of fetching the unbounded global router list.
    chat_model_ids = [
        item["id"]
        for item in models
        if isinstance(item, dict)
        and isinstance(item.get("providers"), list)
        and any(
            isinstance(provider, dict)
            and str(provider.get("task") or "").casefold()
            in {"conversational", "text-generation"}
            and str(provider.get("status") or "").casefold() == "live"
            for provider in item["providers"]
        )
    ]
    router_models = (
        _discover_router_models(query, min(limit, 20), model_ids=chat_model_ids)
        if "models" in kinds and chat_model_ids else []
    )
    by_id = {item["id"]: item for item in models if isinstance(item, dict) and item.get("id")}
    for model in router_models:
        existing = by_id.get(model["id"])
        if existing is None:
            models.append(model)
            by_id[model["id"]] = model
        else:
            existing["providers"] = model["providers"]
    resources.extend(router_models)
    return {
        "status": "metadata_only",
        "query": query,
        "sources_searched": [*kinds, *([ "inference_provider_router" ] if router_models else [])],
        "models": models,
        "router_models": router_models,
        "resources": resources,
        "models_found": len(models),
        "router_models_found": len(router_models),
        "resources_found": len(resources),
        "next_cursor": cursors.get(source) if source != "all" else cursors.get("models"),
        "next_cursors": cursors,
        "weights_downloaded": False,
        "local_model_loaded": False,
    }

def _billable_inference_allowed() -> bool:
    return os.getenv("VORAX_ALLOW_BILLABLE_INFERENCE", "").strip().casefold() in {"1", "true", "yes"}


def _resolve_teacher_model(model_ref: str) -> str:
    """Resolve a live provider and require explicit free pricing unless billable use is enabled."""
    base_model, separator, requested_provider = model_ref.partition(":")
    catalogue = _discover_router_models(base_model, limit=1, model_ids=[base_model])
    model = next((item for item in catalogue if item.get("id") == base_model), None)
    if model is None:
        raise HTTPException(404, "The requested model is not listed by the Inference Providers router")

    providers = [
        item for item in model.get("providers", [])
        if isinstance(item, dict)
        and str(item.get("status", "")).casefold() == "live"
        and isinstance(item.get("provider"), str)
        and item.get("provider")
    ]
    if separator:
        providers = [
            item for item in providers
            if item["provider"].casefold() == requested_provider.casefold()
        ]
    if not _billable_inference_allowed():
        providers = [item for item in providers if item.get("is_free") is True]
    if not providers:
        if not _billable_inference_allowed():
            raise HTTPException(
                402,
                "No live free provider is available for this model; billable inference is disabled by default",
            )
        raise HTTPException(503, "No live Inference Provider is available for the requested model")

    providers.sort(
        key=lambda item: (
            float(item.get("throughput") or 0),
            -float(item.get("first_token_latency_ms") or 1e9),
        ),
        reverse=True,
    )
    return f"{base_model}:{providers[0]['provider']}"


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
    model_id = _resolve_teacher_model(model_id)
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
    provider = headers.get("x-inference-provider") or headers.get("X-Inference-Provider")
    if not isinstance(provider, str) or not provider.strip():
        raise HTTPException(502, "Remote response omitted the Inference Provider identity header")
    provider = provider.strip()
    requested_provider = model_id.rsplit(":", 1)[1] if ":" in model_id else ""
    if requested_provider and provider.casefold() != requested_provider.casefold():
        raise HTTPException(502, "Remote response provider does not match the requested pinned provider")
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
