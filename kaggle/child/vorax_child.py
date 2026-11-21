#!/usr/bin/env python3
"""On-demand VORAX Learning Connector for Kaggle.

This process discovers public metadata, asks remote Teachers, and forwards a
candidate to the trusted HF Runtime. It never clones Home, downloads weights,
runs a local model, executes media generation, processes queues, or writes Vault.
"""
from __future__ import annotations

import argparse
import base64
import json
from collections import deque
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOME_REPO = "musombi123/Revelacode123"
HOME_BRANCH = "main"
TEACHER_URL = os.getenv(
    "VORAX_TEACHER_URL",
    "https://musombi-vorax-teacher-service.hf.space",
).rstrip("/")
RUNTIME_URL = os.getenv(
    "VORAX_RUNTIME_URL",
    "https://musombi-vorax-runtime-service.hf.space",
).rstrip("/")
CHILD_ID = "kaggle-child"
REPORT_PATH = Path(os.getenv("VORAX_REPORT_PATH", "/kaggle/working/vorax-learning-report.json"))
INDEX_VERSION = "vorax-home-method-index/v1"
MAX_HUNTER_SECONDS = 7200
SLICE_QUERIES = {
    "text-docs": "text generation document processing forms extraction",
    "vision-audio": "image understanding vision language audio speech recognition",
    "video-tools": "video understanding video tools API integration",
}
SLICE_MISSING_X = {
    "text-docs": ["documents.ocr", "documents.table_extraction", "documents.layout_understanding"],
    "vision-audio": ["vision.image_understanding", "vision.ocr", "audio.speech_understanding"],
    "video-tools": ["video.scene_understanding", "video.audio_visual_summarization", "tools.api_discovery"],
}


class HunterTimeout(TimeoutError):
    """Kaggle hunter exceeded its two-hour wall-clock budget."""


class ConnectorError(RuntimeError):
    pass


class LocalCircuitBreaker:
    """Per-peer breaker: open after five failures in ten minutes for 60 seconds."""

    def __init__(self):
        self.failures: dict[str, deque[float]] = {}
        self.open_until: dict[str, float] = {}

    def before_request(self, host: str) -> None:
        now = time.monotonic()
        if now < self.open_until.get(host, 0.0):
            raise ConnectorError("Network circuit is open for peer " + host)
        if host in self.open_until:
            self.open_until.pop(host, None)
            self.failures.pop(host, None)

    def record_failure(self, host: str) -> None:
        now = time.monotonic()
        history = self.failures.setdefault(host, deque())
        cutoff = now - 600
        while history and history[0] < cutoff:
            history.popleft()
        history.append(now)
        if len(history) >= 5:
            self.open_until[host] = now + 60

    def record_success(self, host: str) -> None:
        self.failures.pop(host, None)
        self.open_until.pop(host, None)


NETWORK_BREAKER = LocalCircuitBreaker()


def kaggle_secret(name: str, required: bool = False) -> str:
    try:
        from kaggle_secrets import UserSecretsClient
        value = UserSecretsClient().get_secret(name) or ""
    except Exception:
        value = ""
    value = value.strip()
    if required and not value:
        raise ConnectorError(f"Required Kaggle secret is missing: {name}")
    return value



def resolve_task_input(cli_task: str = "", env_task: str = "", secret_task: str = "") -> str:
    """Resolve one task without ever logging its contents; explicit input wins."""
    for candidate in (cli_task, env_task, secret_task):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:12000]
    return ""


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    child_key: str = "",
    headers: dict | None = None,
    timeout: int = 30,
    attempts: int = 3,
) -> tuple[int, dict]:
    """Authenticated HTTPS JSON call with bounded retries and secret-free request logs."""
    if not base_url.startswith("https://"):
        raise ConnectorError("VORAX network calls require HTTPS")
    if timeout < 1 or timeout > 30 or attempts < 1 or attempts > 3:
        raise ConnectorError("Network timeout and retry limits exceed VORAX policy")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "VORAX-Kaggle-Learning-Connector/1.0",
    }
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    if child_key:
        request_headers["x-child-id"] = CHILD_ID
        request_headers["x-child-key"] = child_key
    if headers:
        request_headers.update({str(key): str(value) for key, value in headers.items()})
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=request_headers,
        method=method,
    )
    request_id = uuid.uuid4().hex
    host = urllib.parse.urlparse(base_url).netloc
    last_error = None
    for attempt in range(1, attempts + 1):
        NETWORK_BREAKER.before_request(host)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(3 * 1024 * 1024 + 1)
                if len(raw) > 3 * 1024 * 1024:
                    raise ConnectorError("Remote response exceeded the 3 MB connector limit")
                decoded = json.loads(raw.decode("utf-8"))
                if not isinstance(decoded, dict):
                    raise ConnectorError("Remote service returned an unexpected JSON shape")
                NETWORK_BREAKER.record_success(host)
                print(json.dumps({
                    "event": "network_request",
                    "request_id": request_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "method": method.upper(),
                    "host": host,
                    "status": response.status,
                    "attempt": attempt,
                }))
                return response.status, decoded
        except urllib.error.HTTPError as exc:
            last_error = ConnectorError(f"Remote service returned HTTP {exc.code}")
            retryable = exc.code in {408, 425, 429} or exc.code >= 500
            if retryable:
                NETWORK_BREAKER.record_failure(host)
            else:
                NETWORK_BREAKER.record_success(host)
            print(json.dumps({
                "event": "network_request",
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "method": method.upper(),
                "host": host,
                "status": exc.code,
                "attempt": attempt,
            }))
            if not retryable:
                break
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ConnectorError) as exc:
            last_error = exc if isinstance(exc, ConnectorError) else ConnectorError(type(exc).__name__)
            NETWORK_BREAKER.record_failure(host)
            print(json.dumps({
                "event": "network_request",
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "method": method.upper(),
                "host": host,
                "status": "transport_error",
                "error": type(exc).__name__,
                "attempt": attempt,
            }))
        if attempt < attempts:
            time.sleep(float(2 ** (attempt - 1)))
    raise last_error or ConnectorError("Remote request failed")


def read_home_metadata_index(read_token: str, child_key: str = "") -> dict:
    path = (
        f"/repos/{HOME_REPO}/contents/templates/library/index.json"
        f"?ref={urllib.parse.quote(HOME_BRANCH, safe='')}"
    )
    index = None
    direct_error = None
    if read_token:
        try:
            _, blob = request_json(
                "https://api.github.com",
                path,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": "Bearer " + read_token,
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=30,
                attempts=3,
            )
            if blob.get("encoding") != "base64":
                raise ConnectorError("Home metadata index is not a base64 JSON file")
            index = json.loads(base64.b64decode(blob["content"]).decode("utf-8"))
        except Exception as exc:
            direct_error = type(exc).__name__
    else:
        direct_error = "home_read_token_unavailable"

    if index is None and child_key:
        try:
            _, cached = request_json(
                RUNTIME_URL,
                "/vorax/home-index",
                child_key=child_key,
                timeout=30,
                attempts=3,
            )
            if cached.get("method_bodies_included") is not False:
                raise ConnectorError("Runtime Home fallback unexpectedly included method bodies")
            index = cached
        except Exception as exc:
            raise ConnectorError(
                "Home metadata index and Runtime's cached metadata snapshot are both unavailable "
                f"(Home={direct_error}, Runtime={type(exc).__name__})"
            ) from exc
    if (
        not isinstance(index, dict)
        or index.get("version") != INDEX_VERSION
        or not isinstance(index.get("records"), list)
        or index.get("methods_total") != len(index["records"])
    ):
        raise ConnectorError("Home metadata index failed its schema check")
    for item in index["records"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"id", "domain", "task", "triggers", "method_sha256"}
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("domain"), str)
            or not isinstance(item.get("task"), str)
            or not isinstance(item.get("triggers"), list)
            or any(not isinstance(value, str) for value in item.get("triggers", []))
            or not isinstance(item.get("method_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", item.get("method_sha256", ""))
        ):
            raise ConnectorError("Home index contains an invalid metadata/checksum entry")
    return index


def discover_world(query: str, page_limit: int = 3) -> dict:
    """Page through model, Space and Dataset metadata; never request repository files or weights."""
    encoded = urllib.parse.urlencode({
        "query": query[:160],
        "limit": "20",
        "source": "all",
    })
    _, first = request_json(TEACHER_URL, "/teacher/discover?" + encoded, timeout=30)
    if first.get("status") != "metadata_only" or first.get("weights_downloaded") is not False:
        raise ConnectorError("Teacher did not confirm metadata-only discovery")

    models = list(first.get("models") or [])
    resources = list(first.get("resources") or [])
    source_cursors = dict(first.get("next_cursors") or {})
    pages_by_source = {"models": 1, "spaces": 1, "datasets": 1}
    remaining = {}

    for source in ("models", "spaces", "datasets"):
        cursor = source_cursors.get(source)
        while cursor and pages_by_source[source] < page_limit:
            next_query = urllib.parse.urlencode({
                "query": query[:160],
                "limit": "20",
                "source": source,
                "cursor": cursor,
            })
            _, page = request_json(TEACHER_URL, "/teacher/discover?" + next_query, timeout=30)
            if page.get("status") != "metadata_only" or page.get("weights_downloaded") is not False:
                raise ConnectorError(f"Teacher {source} pagination did not remain metadata-only")
            page_resources = page.get("resources") or []
            resources.extend(page_resources)
            if source == "models":
                models.extend(page.get("models") or [])
            page_cursors = dict(page.get("next_cursors") or {source: page.get("next_cursor")})
            cursor = page_cursors.get(source)
            pages_by_source[source] += 1
        remaining[source] = bool(cursor)
    return {
        "status": "metadata_only",
        "query": query,
        "models": models,
        "resources": resources,
        "models_found": len(models),
        "resources_found": len(resources),
        "pages_read": sum(pages_by_source.values()),
        "pages_by_source": pages_by_source,
        "pagination_remaining": any(remaining.values()),
        "pagination_remaining_by_source": remaining,
        "weights_downloaded": False,
        "local_model_loaded": False,
    }


def choose_teacher_models(models: list[dict], requested: str = "", count: int = 3) -> list[str]:
    """Choose distinct live chat models across providers; an explicit model is only prioritised."""
    if not models:
        raise ConnectorError("Teacher metadata discovery returned no models")

    requested_value = requested.strip()
    requested_id, separator, requested_provider = requested_value.partition(":")
    ordered = list(models)
    if requested_id:
        ordered.sort(key=lambda item: 0 if str(item.get("id") or "").strip() == requested_id else 1)

    allow_billable = os.getenv("VORAX_ALLOW_BILLABLE_INFERENCE", "").strip().casefold() in {"1", "true", "yes"}
    selected = []
    selected_model_ids = set()
    for item in ordered:
        model_id = str(item.get("id") or "").strip()
        if not model_id or model_id in selected_model_ids:
            continue
        providers = [
            provider for provider in item.get("providers", [])
            if isinstance(provider, dict)
            and str(provider.get("status", "")).casefold() == "live"
            and str(provider.get("task", "")).casefold() in {"conversational", "text-generation"}
            and str(provider.get("provider") or "").strip()
            and (allow_billable or provider.get("is_free") is True)
        ]
        if requested_id and model_id == requested_id and separator:
            providers = [
                provider for provider in providers
                if str(provider.get("provider") or "").casefold() == requested_provider.casefold()
            ]
        if not providers:
            continue
        providers.sort(
            key=lambda provider: (
                float(provider.get("throughput") or 0),
                -float(provider.get("first_token_latency_ms") or 1e9),
            ),
            reverse=True,
        )
        provider = str(providers[0]["provider"]).strip()
        selected.append(f"{model_id}:{provider}")
        selected_model_ids.add(model_id)
        if len(selected) >= count:
            break

    if requested_id and not any(model_ref.split(":", 1)[0] == requested_id for model_ref in selected):
        raise ConnectorError(
            "The requested model/provider is not live in the current metadata catalogue"
        )
    if not selected:
        raise ConnectorError("No live conversational Inference Provider models were found")
    return selected


def ask_remote_teacher(prompt: str, model_id: str, child_key: str) -> dict:
    _, response = request_json(
        TEACHER_URL,
        "/teacher/ask",
        method="POST",
        payload={"prompt": prompt[:12000], "model_id": model_id},
        child_key=child_key,
        timeout=30,
    )
    if response.get("remote_inference") is not True:
        raise ConnectorError(f"Teacher {model_id} did not confirm remote Inference Provider execution")
    if response.get("kaggle_t4_used") is not False:
        raise ConnectorError(f"Teacher {model_id} returned incorrect Kaggle GPU provenance")
    if response.get("weights_downloaded") is not False:
        raise ConnectorError(f"Teacher {model_id} reported a model weight download")
    if not isinstance(response.get("response"), str) or not response["response"].strip():
        raise ConnectorError(f"Teacher {model_id} returned no teaching response")
    return response

def _candidate_from_teacher(response: str) -> dict:
    text = response.strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        text = re.sub(r"^" + fence + r"(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*" + fence + r"$", "", text)
    try:
        candidate = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConnectorError("Teacher response was not a JSON V-Lang proposal") from exc
    if isinstance(candidate, dict) and isinstance(candidate.get("candidate"), dict):
        candidate = candidate["candidate"]
    if not isinstance(candidate, dict):
        raise ConnectorError("Teacher response did not contain one V-Lang method")
    return candidate


def home_duplicate_reason(index: dict, candidate: dict) -> str | None:
    candidate_id = candidate.get("id")
    domain = str(candidate.get("domain", "")).casefold()
    task = str(candidate.get("task", "")).casefold()
    triggers = {str(x).casefold() for x in candidate.get("triggers", [])}
    for known in index["records"]:
        if known["id"] == candidate_id:
            return "method_id_exists_in_home"
        if known["domain"].casefold() == domain and known["task"].casefold() == task:
            return "method_task_exists_in_home"
        known_triggers = {str(x).casefold() for x in known["triggers"]}
        if domain == known["domain"].casefold() and triggers and triggers.intersection(known_triggers):
            return "method_trigger_exists_in_home"
    return None


def save_report(report: dict) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("Report:", REPORT_PATH)
    print(json.dumps(report, indent=2, ensure_ascii=False))


def assess_discovered_capabilities(items: list[dict]) -> tuple[list[dict], int]:
    """Record known metadata fields and explicit unknowns; do not invent execution evidence."""
    assessments = []
    understood = 0
    seen = set()
    for item in items[:120]:
        if not isinstance(item, dict):
            continue
        resource_id = item.get("id")
        source_type = item.get("source_type")
        if not isinstance(resource_id, str) or not isinstance(source_type, str):
            continue
        key = (source_type, resource_id)
        if key in seen:
            continue
        seen.add(key)
        tags = [str(tag)[:100] for tag in item.get("tags", []) if isinstance(tag, (str, int))]
        providers = [
            str(provider.get("provider"))[:80]
            for provider in item.get("providers", [])
            if isinstance(provider, dict) and provider.get("provider")
        ]
        pipeline = item.get("pipeline_tag") if isinstance(item.get("pipeline_tag"), str) else None
        sdk = item.get("sdk") if isinstance(item.get("sdk"), str) else None
        library = item.get("library_name") if isinstance(item.get("library_name"), str) else None
        description = item.get("description") if isinstance(item.get("description"), str) else ""
        task_label = pipeline or (tags[0] if tags else source_type)
        known_requirements = [value for value in (sdk, library) if value]
        enough_metadata = bool(task_label and (known_requirements or providers) and (description or tags))
        if enough_metadata:
            understood += 1
        limitations = ["metadata_only", "actual_inputs_and_outputs_not_execution_verified"]
        if not providers:
            limitations.append("no_remote_inference_provider_declared_in_metadata")
        assessments.append({
            "resource_id": resource_id,
            "source_type": source_type,
            "capability_or_task": task_label,
            "inputs": "not_declared_by_public_metadata",
            "outputs": pipeline or "not_declared_by_public_metadata",
            "limitations": limitations,
            "requirements": {
                "sdk": sdk,
                "library": library,
                "remote_providers": providers[:20],
                "tags": tags[:20],
            },
            "metadata_understood": enough_metadata,
            "execution_verified": False,
            "weights_downloaded": False,
        })
    return assessments, understood


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a bounded VORAX learning or weekly hunter pass.")
    parser.add_argument("--task", default="")
    parser.add_argument("--model-id", default=os.getenv("VORAX_TEACHER_MODEL", ""))
    parser.add_argument("--discovery-query", default="")
    parser.add_argument("--teacher-count", type=int, default=int(os.getenv("VORAX_TEACHER_COUNT", "5")))
    parser.add_argument("--discovery-pages", type=int, default=int(os.getenv("VORAX_DISCOVERY_PAGES", "3")))
    parser.add_argument("--cycle-id", default="")
    parser.add_argument("--hunter-id", default="")
    parser.add_argument("--hunter-slice", choices=tuple(SLICE_QUERIES), default=os.getenv("VORAX_HUNTER_SLICE", ""))
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument("--cycle-task", default="")
    parser.add_argument("--max-runtime-seconds", type=int, default=MAX_HUNTER_SECONDS)
    args = parser.parse_args()

    cycle_mode = bool(args.cycle_id or args.hunter_id or args.hunter_slice)
    if args.teacher_count < 1 or args.teacher_count > 5:
        raise ConnectorError("--teacher-count must be between 1 and 5")
    if args.discovery_pages < 1 or args.discovery_pages > 20:
        raise ConnectorError("--discovery-pages must be between 1 and 20")
    if args.max_runtime_seconds < 60 or args.max_runtime_seconds > MAX_HUNTER_SECONDS:
        raise ConnectorError("--max-runtime-seconds must be between 60 and 7200")
    if cycle_mode and (not args.cycle_id or not args.hunter_id or not args.hunter_slice):
        raise ConnectorError("Weekly hunter mode requires --cycle-id, --hunter-id and --hunter-slice")
    if cycle_mode and args.hunter_id not in {"hunter-1", "hunter-2", "hunter-3"}:
        raise ConnectorError("Unknown weekly hunter ID")
    if cycle_mode and args.attempt not in {0, 1}:
        raise ConnectorError("Weekly hunter attempt must be 0 or 1")
    if cycle_mode and (
        (args.hunter_id == "hunter-1" and args.hunter_slice != "text-docs")
        or (args.hunter_id == "hunter-2" and args.hunter_slice != "vision-audio")
        or (args.hunter_id == "hunter-3" and args.hunter_slice != "video-tools")
    ):
        raise ConnectorError("Hunter ID does not match its assigned discovery slice")

    cycle_task = None
    if cycle_mode and args.cycle_task:
        try:
            cycle_task = json.loads(args.cycle_task)
        except json.JSONDecodeError as exc:
            raise ConnectorError("--cycle-task must be a JSON object") from exc
        if not isinstance(cycle_task, dict):
            raise ConnectorError("--cycle-task must be a JSON object")
    if cycle_mode and cycle_task and args.hunter_id != "hunter-1":
        raise ConnectorError("Only the Text + Docs hunter may receive the text curriculum task")

    return_code = 1
    child_key = ""
    runtime_delivery_attempted = False
    alarm_active = False
    report: dict[str, Any] = {
        "child": CHILD_ID,
        "cycle_id": args.cycle_id or None,
        "hunter_id": args.hunter_id or None,
        "discovery_slice": args.hunter_slice or None,
        "attempt": args.attempt if cycle_mode else None,
        "status": "starting",
        "home": {"repository": HOME_REPO, "branch": HOME_BRANCH},
        "teacher": {"url": TEACHER_URL, "live": False},
        "runtime": {"url": RUNTIME_URL, "live": False},
        "discovery": {"status": "not_started"},
        "knowledge": {"status": "not_requested"},
        "discovered_count": 0,
        "understood_count": 0,
        "capability_assessments": [],
        "acquired_count": 0,
        "verified_count": 0,
        "added_count": 0,
        "rejected_count": 0,
        "rejected": [],
        "missing_X_list": SLICE_MISSING_X.get(args.hunter_slice, []) if cycle_mode else [],
        "next_priority": SLICE_MISSING_X.get(args.hunter_slice, []) if cycle_mode else [],
        "cached_proposals": [],
        "runtime_delivery": {"status": "not_applicable"},
        "controls": {
            "weights_downloaded": False,
            "local_model_framework_imported": False,
            "local_model_loaded": False,
            "media_generation_attempted": False,
            "automatic_queue_processing": cycle_mode,
            "direct_vault_write": False,
            "home_method_bodies_downloaded": False,
            "kaggle_gpu_used": False,
            "billable_inference_allowed": False,
            "free_provider_only": True,
            "max_runtime_seconds": args.max_runtime_seconds,
        },
        "secret_use": {},
        "failure": None,
    }

    def _timeout_handler(signum, frame):
        raise HunterTimeout("Hunter exceeded the two-hour execution deadline")

    try:
        if cycle_mode and hasattr(signal, "SIGALRM"):
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(args.max_runtime_seconds)
            alarm_active = True

        home_read = kaggle_secret("VORAX_HOME_READ_TOKEN", required=False)
        child_key = kaggle_secret("VORAX_KAGGLE_CHILD_API_KEY", required=True)

        if cycle_mode:
            # Deliberately never read VORAX_X or its Kaggle secret in weekly cycle mode.
            task_secret = ""
            if cycle_task:
                task_input = json.dumps({
                    "assigned_method": cycle_task.get("id"),
                    "domain": cycle_task.get("domain"),
                    "task": cycle_task.get("task"),
                    "triggers": cycle_task.get("triggers"),
                    "inputs": cycle_task.get("inputs"),
                    "mandatory_test_vectors": cycle_task.get("tests"),
                    "operation_chain": cycle_task.get("operation_chain"),
                    "ordered_inputs": cycle_task.get("ordered_inputs"),
                    "curriculum_kind": cycle_task.get("curriculum_kind"),
                }, ensure_ascii=False)
            else:
                task_input = ""
        else:
            task_secret = kaggle_secret("VORAX_X", required=False)
            task_input = resolve_task_input(args.task, os.getenv("VORAX_X", ""), task_secret)

        report["secret_use"] = {
            "VORAX_HOME_READ_TOKEN": {
                "available": bool(home_read),
                "used_for": "direct_metadata_index_read_only" if home_read else "Runtime_cached_metadata_fallback",
            },
            "VORAX_HOME_WRITE_TOKEN": {"requested_by_connector": False, "authority": "Runtime_only"},
            "VORAX_KAGGLE_CHILD_API_KEY": {"available": True, "used_for": "Teacher_and_Runtime_auth"},
            "VORAX_X": {
                "requested_by_connector": not cycle_mode,
                "available": bool(task_secret) if not cycle_mode else False,
                "content_logged": False,
                "used_for": "manual_one_run_task" if not cycle_mode and task_secret else "never_read_in_weekly_cycle",
            },
            "HF_VAULT_TOKEN": {"requested_by_connector": False, "authority": "Runtime_only"},
            "HF_READ_TOKEN": {"requested_by_connector": False, "authority": "Teacher_only"},
            "LEGEND_TEACHER_API_KEY": {"requested_by_connector": False, "authority": "Teacher_only"},
            "LEGEND_TEACHER_MODEL": {"requested_by_connector": False, "authority": "configured_on_Teacher_or_cli"},
            "LEGEND_TEACHER_BASE_URL": {"requested_by_connector": False, "authority": "Teacher_service_URL"},
        }

        teacher_code, teacher_health = request_json(TEACHER_URL, "/health")
        runtime_code, runtime_health = request_json(RUNTIME_URL, "/health")
        if teacher_code != 200 or teacher_health.get("service") != "teacher":
            raise ConnectorError("Teacher health identity check failed")
        if runtime_code != 200 or runtime_health.get("service") != "runtime":
            raise ConnectorError("Runtime health identity check failed")
        report["teacher"]["live"] = True
        report["teacher"]["health"] = teacher_health
        report["runtime"]["live"] = True
        report["runtime"]["health"] = runtime_health

        home_index = read_home_metadata_index(home_read, child_key)
        report["home"]["index_methods"] = home_index.get("methods_total")
        report["home"]["index_source_status"] = home_index.get("index_source_status", {"status": "direct_read"})
        report["home"]["index_content"] = "metadata_only"
        report["home"]["method_bodies_downloaded"] = False

        query = args.discovery_query.strip()
        if cycle_mode and not query:
            query = SLICE_QUERIES[args.hunter_slice]
        discovery = discover_world(query, page_limit=args.discovery_pages)
        report["discovery"] = {
            key: discovery.get(key)
            for key in (
                "status", "query", "models_found", "resources_found",
                "pages_read", "pages_by_source", "pagination_remaining",
                "pagination_remaining_by_source", "weights_downloaded",
            )
        }
        report["discovery"]["slice"] = args.hunter_slice if cycle_mode else None
        discovered = list(discovery.get("models") or []) + list(discovery.get("resources") or [])
        assessments, understood_count = assess_discovered_capabilities(discovered)
        report["capability_assessments"] = assessments
        if not discovered:
            raise ConnectorError("Teacher discovery returned no metadata for the assigned slice")
        home_metadata_text = " ".join(
            str(value)
            for record in home_index.get("records", [])
            if isinstance(record, dict)
            for value in (
                record.get("id", ""),
                record.get("domain", ""),
                record.get("task", ""),
                *(record.get("triggers", []) if isinstance(record.get("triggers"), list) else []),
            )
        ).casefold()
        discovered_gaps = []
        for assessment in assessments:
            capability = assessment.get("capability_or_task")
            source_type = assessment.get("source_type")
            if not isinstance(capability, str) or not isinstance(source_type, str):
                continue
            slug = re.sub(r"[^a-z0-9]+", "_", capability.casefold()).strip("_")[:64]
            if slug and slug.replace("_", " ") not in home_metadata_text:
                discovered_gaps.append(f"capability.{source_type}.{slug}")
        report["missing_X_list"] = list(dict.fromkeys(
            report["missing_X_list"] + discovered_gaps
        ))[:100]
        report["next_priority"] = list(dict.fromkeys(
            report["next_priority"] + discovered_gaps
        ))[:100]
        # Reaching the configured page cap is a successful bounded discovery pass.
        # The next weekly cycle starts fresh and records any remaining pages again.

        report["discovered_count"] = len(assessments)
        report["understood_count"] = understood_count
        report["status"] = "connector_ready"

        if runtime_health.get("ready") is not True:
            # Metadata discovery is still reportable, but never send a method proposal
            # when Runtime cannot prove its Vault/Home write path is ready.
            report["knowledge"] = {
                "status": "paused_runtime_not_ready",
                "note": "Discovery completed; method acquisition was paused because Runtime reports ready=false.",
            }
            task_input = ""
        elif not task_input:
            report["knowledge"] = {
                "status": "discovery_only",
                "note": (
                    "This hunter slice has no eligible text curriculum task. Metadata discovery is recorded; "
                    "no remote multimodal inference or method verification is claimed."
                ),
            }
        else:
            teacher_models = choose_teacher_models(
                discovery.get("models") or [],
                args.model_id,
                count=args.teacher_count,
            )
            if not teacher_models:
                raise ConnectorError("No live free conversational teacher models were available for this task")
            base_prompt = (
                "Teach VORAX how to perform the assigned task as one bounded V-Lang v1 method. "
                "Return exactly one JSON method object. Use only registered deterministic V-Lang operations; "
                "do not use CALL TOOL. Include every mandatory test vector exactly as supplied and may add "
                "more tests. Use the exact assigned id, domain, task, input schema and triggers. "
                "When operation_chain and ordered_inputs are supplied, use every operation in that exact "
                "order and preserve the specified input/step reference order; do not add, omit, reorder or "
                "substitute operations. "
                "No code, shell, URLs, secrets, weights, model configs, guessed tool outputs or fake media claims. "
                "The trusted Runtime independently executes all declared tests.\n"
                "Assigned task specification:\n" + task_input
            )

            consultations = []
            initial_model = teacher_models[0]
            initial_response = ask_remote_teacher(base_prompt, initial_model, child_key)
            consultations.append({
                "model_id": initial_model,
                "provider_used": initial_response.get("provider_used"),
                "remote_inference": True,
                "kaggle_t4_used": False,
                "role": "initial_proposer",
            })
            initial_candidate = _candidate_from_teacher(initial_response["response"])
            candidate = initial_candidate
            final_model = initial_model
            final_response = initial_response
            reviews = []

            for reviewer_model in teacher_models[1:-1]:
                review_prompt = (
                    "Review this V-Lang method for mathematical correctness, safe bounded operations and "
                    "mandatory test coverage. Return JSON with issues, improvements and test_gaps. "
                    "Do not return code or claim execution.\n"
                    + json.dumps(initial_candidate, ensure_ascii=False)[:6500]
                )
                review = ask_remote_teacher(review_prompt, reviewer_model, child_key)
                reviews.append({"model_id": reviewer_model, "review": review["response"][:3500]})
                consultations.append({
                    "model_id": reviewer_model,
                    "provider_used": review.get("provider_used"),
                    "remote_inference": True,
                    "kaggle_t4_used": False,
                    "role": "independent_reviewer",
                })

            if len(teacher_models) > 1:
                final_model = teacher_models[-1]
                synthesis_prompt = (
                    "Synthesize the initial V-Lang proposal and independent review findings into exactly "
                    "one JSON V-Lang v1 method. Preserve the exact assigned id, domain, task, inputs and "
                    "all required triggers/tests. Use only registered deterministic operations; do not use "
                    "CALL TOOL. No code, shell, URLs, secrets, weights, model config or fake execution claims.\n"
                    "Assigned task:\n" + task_input
                    + "\nProposal:\n" + json.dumps(initial_candidate, ensure_ascii=False)[:5000]
                    + "\nReviews:\n" + json.dumps(reviews, ensure_ascii=False)[:5000]
                )
                final_response = ask_remote_teacher(synthesis_prompt, final_model, child_key)
                candidate = _candidate_from_teacher(final_response["response"])
                consultations.append({
                    "model_id": final_model,
                    "provider_used": final_response.get("provider_used"),
                    "remote_inference": True,
                    "kaggle_t4_used": False,
                    "role": "method_synthesizer",
                })

            if cycle_mode and cycle_task:
                for field in ("id", "domain", "task", "inputs"):
                    if candidate.get(field) != cycle_task.get(field):
                        raise ConnectorError(f"Teacher candidate changed assigned curriculum field: {field}")
                required_tests = {
                    json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                    for item in cycle_task.get("tests", [])
                }
                candidate_tests = {
                    json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                    for item in candidate.get("tests", [])
                }
                if not required_tests.issubset(candidate_tests):
                    raise ConnectorError("Teacher candidate omitted mandatory curriculum test vectors")

            report["knowledge"]["teacher_panel"] = {
                "requested_count": args.teacher_count,
                "consulted_count": len(consultations),
                "consultations": consultations,
                "all_remote": all(item["remote_inference"] is True for item in consultations),
                "kaggle_t4_used": False,
            }
            report["cached_proposals"].append({
                "method": candidate,
                "teacher_response": final_response["response"],
                "teacher_attestation": final_response.get("attestation"),
                "teacher_model_id": final_model,
                "provider_used": final_response.get("provider_used"),
                "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })

            duplicate = home_duplicate_reason(home_index, candidate)
            if duplicate:
                report["knowledge"].update({
                    "status": "already_in_home",
                    "reason": duplicate,
                    "method_id": candidate.get("id"),
                })
                report["rejected_count"] += 1
                report["rejected"].append({"method_id": candidate.get("id"), "reason": duplicate})
            else:
                # Persist proposal evidence to the Kaggle output before contacting Runtime.
                save_report(report)
                status, runtime_result = request_json(
                    RUNTIME_URL,
                    "/vorax/verify",
                    method="POST",
                    payload={
                        "method": candidate,
                        "teacher_response": final_response["response"],
                        "teacher_attestation": final_response["attestation"],
                        "x": task_input,
                        "teacher_model_id": final_model,
                        "child_id": CHILD_ID,
                        "api_key": child_key,
                        **({
                            "cycle_id": args.cycle_id,
                            "hunter_id": args.hunter_id,
                            "attempt": args.attempt,
                        } if cycle_mode else {}),
                    },
                    child_key=child_key,
                    timeout=30,
                )
                report["knowledge"].update({
                    "status": runtime_result.get("status"),
                    "teacher_model_id": final_model,
                    "provider_used": final_response.get("provider_used"),
                    "remote_inference": final_response.get("remote_inference"),
                    "kaggle_t4_used": final_response.get("kaggle_t4_used"),
                    "method_id": runtime_result.get("method"),
                    "method_sha256": runtime_result.get("method_sha256"),
                    "verification": runtime_result.get("verification"),
                    "vault": runtime_result.get("vault"),
                    "home_submission": runtime_result.get("home_submission"),
                    "http_status": status,
                })
                if runtime_result.get("status") == "verified":
                    report["acquired_count"] += 1
                    report["verified_count"] += 1
                    if (runtime_result.get("home_submission") or {}).get("status") == "merged_and_promoted":
                        report["added_count"] += 1
                elif runtime_result.get("status") in {"blocked", "duplicate"}:
                    report["rejected_count"] += 1
                    report["rejected"].append({
                        "method_id": candidate.get("id"),
                        "reason": runtime_result.get("reason") or runtime_result.get("status"),
                    })
                else:
                    raise ConnectorError("Runtime returned an unsupported verification status")

        return_code = 0
        if report["knowledge"].get("status") == "paused_runtime_not_ready":
            return_code = 1

    except Exception as exc:
        report["status"] = "connector_blocked"
        report["failure"] = type(exc).__name__ + ": " + str(exc)[:400]
        return_code = 1
    finally:
        if alarm_active:
            signal.alarm(0)
        if cycle_mode:
            report["status"] = "completed" if report.get("failure") is None else "failed"
            report["runtime_delivery"] = {"status": "pending_retry", "attempts": 0}
            # Local report is durable as a Kaggle kernel output before Runtime delivery.
            save_report(report)
            if child_key:
                try:
                    delivery_status, delivery = request_json(
                        RUNTIME_URL,
                        "/vorax/cycle/hunter-report",
                        method="POST",
                        payload={
                            "cycle_id": args.cycle_id,
                            "hunter_id": args.hunter_id,
                            "attempt": args.attempt,
                            "report": report,
                        },
                        child_key=child_key,
                        timeout=30,
                        attempts=3,
                    )
                    report["runtime_delivery"] = {
                        "status": "delivered",
                        "http_status": delivery_status,
                        "runtime_status": delivery.get("status"),
                        "attempts": 3,
                    }
                except Exception as exc:
                    report["runtime_delivery"] = {
                        "status": "pending_retry",
                        "error": type(exc).__name__,
                        "attempts": 3,
                        "local_cache": str(REPORT_PATH),
                    }
                    return_code = 1
                save_report(report)
            else:
                report["runtime_delivery"] = {
                    "status": "pending_retry",
                    "reason": "child_authentication_not_available",
                    "local_cache": str(REPORT_PATH),
                }
                save_report(report)
        else:
            save_report(report)

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
