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
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
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
REPORT_PATH = Path("/kaggle/working/vorax-learning-report.json")
INDEX_VERSION = "vorax-home-method-index/v1"


class ConnectorError(RuntimeError):
    pass


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


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    child_key: str = "",
    timeout: int = 45,
) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/json",
        "User-Agent": "VORAX-Kaggle-Learning-Connector/1.0",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    if child_key:
        headers["x-child-id"] = CHILD_ID
        headers["x-child-key"] = child_key
    request = urllib.request.Request(
        base_url + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(3 * 1024 * 1024 + 1)
            if len(raw) > 3 * 1024 * 1024:
                raise ConnectorError("Remote response exceeded the 3 MB connector limit")
            decoded = json.loads(raw.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ConnectorError("Remote service returned an unexpected JSON shape")
            return response.status, decoded
    except urllib.error.HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", "replace")
        raise ConnectorError(
            f"Remote service returned HTTP {exc.code}: {detail[:500]}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ConnectorError("Remote request failed: " + type(exc).__name__) from exc


def read_home_metadata_index(read_token: str) -> dict:
    path = (
        f"/repos/{HOME_REPO}/contents/templates/library/index.json"
        f"?ref={urllib.parse.quote(HOME_BRANCH, safe='')}"
    )
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + read_token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "VORAX-Kaggle-Learning-Connector",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            blob = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ConnectorError(
            f"Home metadata index could not be read (HTTP {exc.code}); Children never fetch method bodies"
        ) from exc
    if blob.get("encoding") != "base64":
        raise ConnectorError("Home metadata index is not a base64 JSON file")
    try:
        index = json.loads(base64.b64decode(blob["content"]).decode("utf-8"))
    except Exception as exc:
        raise ConnectorError("Home metadata index is malformed") from exc
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
            or set(item) != {"id", "domain", "task", "triggers"}
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("domain"), str)
            or not isinstance(item.get("task"), str)
            or not isinstance(item.get("triggers"), list)
        ):
            raise ConnectorError("Home index contains an invalid metadata entry")
    return index


def discover_world(query: str, page_limit: int = 5) -> dict:
    encoded = urllib.parse.urlencode({
        "query": query[:160],
        "limit": "100",
        "source": "all",
    })
    _, first = request_json(TEACHER_URL, "/teacher/discover?" + encoded, timeout=60)
    if first.get("status") != "metadata_only" or first.get("weights_downloaded") is not False:
        raise ConnectorError("Teacher did not confirm metadata-only discovery")
    models = list(first.get("models") or [])
    resources = list(first.get("resources") or [])
    cursor = first.get("next_cursor")
    pages = 1

    while cursor and pages < page_limit:
        next_query = urllib.parse.urlencode({
            "query": query[:160],
            "limit": "100",
            "source": "models",
            "cursor": cursor,
        })
        _, page = request_json(TEACHER_URL, "/teacher/discover?" + next_query, timeout=60)
        if page.get("weights_downloaded") is not False:
            raise ConnectorError("Teacher discovery reported a weight download")
        page_models = page.get("models") or []
        models.extend(page_models)
        resources.extend(page_models)
        cursor = page.get("next_cursor")
        pages += 1

    return {
        "status": "metadata_only",
        "query": query,
        "models": models,
        "resources": resources,
        "models_found": len(models),
        "resources_found": len(resources),
        "pages_read": pages,
        "pagination_remaining": bool(cursor),
        "weights_downloaded": False,
        "local_model_loaded": False,
    }


def choose_teacher_model(models: list[dict], requested: str = "") -> str:
    if requested.strip():
        return requested.strip()
    if not models:
        raise ConnectorError("Teacher metadata discovery returned no models to query")
    for item in models:
        pipeline = str(item.get("pipeline_tag") or "").casefold()
        tags = " ".join(str(x).casefold() for x in item.get("tags", []))
        model_id = str(item.get("id") or "")
        if "text-generation" in pipeline or "text-generation" in tags or "instruct" in model_id.casefold():
            return model_id
    return str(models[0].get("id") or "")


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one on-demand VORAX knowledge-gathering pass.")
    parser.add_argument("--task", default=os.getenv("VORAX_X", ""))
    parser.add_argument("--model-id", default=os.getenv("VORAX_TEACHER_MODEL", ""))
    parser.add_argument("--discovery-query", default="")
    args = parser.parse_args()

    report: dict[str, Any] = {
        "child": CHILD_ID,
        "status": "starting",
        "home": {"repository": HOME_REPO, "branch": HOME_BRANCH},
        "teacher": {"url": TEACHER_URL, "live": False},
        "runtime": {"url": RUNTIME_URL, "live": False},
        "discovery": {"status": "not_started"},
        "knowledge": {"status": "not_requested"},
        "controls": {
            "weights_downloaded": False,
            "diffusers_imported": False,
            "local_model_loaded": False,
            "media_generation_attempted": False,
            "automatic_queue_processing": False,
            "direct_vault_write": False,
            "home_method_bodies_downloaded": False,
            "kaggle_gpu_used": False,
        },
        "secret_use": {},
        "failure": None,
    }

    try:
        home_read = kaggle_secret("VORAX_HOME_READ_TOKEN", required=True)
        home_write = kaggle_secret("VORAX_HOME_WRITE_TOKEN", required=True)
        child_key = kaggle_secret("VORAX_KAGGLE_CHILD_API_KEY", required=True)
        vault_token = kaggle_secret("HF_VAULT_TOKEN", required=True)
        hf_read = kaggle_secret("HF_READ_TOKEN")
        legacy_teacher_key = kaggle_secret("LEGEND_TEACHER_API_KEY")
        legacy_teacher_model = kaggle_secret("LEGEND_TEACHER_MODEL")
        legacy_teacher_base = kaggle_secret("LEGEND_TEACHER_BASE_URL")

        report["secret_use"] = {
            "VORAX_HOME_READ_TOKEN": {"available": True, "used_for": "direct_metadata_index_read_only"},
            "VORAX_HOME_WRITE_TOKEN": {"available": bool(home_write), "used": False,
                                       "note": "Runtime owns GitHub proposal submission"},
            "VORAX_KAGGLE_CHILD_API_KEY": {"available": True, "used_for": "Teacher_and_Runtime_auth"},
            "HF_VAULT_TOKEN": {"available": bool(vault_token), "used": False,
                               "note": "Runtime alone writes to Vault"},
            "HF_READ_TOKEN": {"available": bool(hf_read), "used": False,
                              "note": "Teacher owns Hub metadata discovery"},
            "LEGEND_TEACHER_API_KEY": {"available": bool(legacy_teacher_key), "used": False},
            "LEGEND_TEACHER_MODEL": {"available": bool(legacy_teacher_model), "used": False},
            "LEGEND_TEACHER_BASE_URL": {"available": bool(legacy_teacher_base), "used": False},
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

        home_index = read_home_metadata_index(home_read)
        report["home"]["index_methods"] = home_index.get("methods_total")
        report["home"]["index_content"] = "metadata_only"
        report["home"]["method_bodies_downloaded"] = False

        query = args.discovery_query.strip() or args.task.strip()
        discovery = discover_world(query, page_limit=5)
        report["discovery"] = {
            key: discovery.get(key)
            for key in (
                "status", "query", "models_found", "resources_found",
                "pages_read", "pagination_remaining", "weights_downloaded",
            )
        }
        if not discovery["models"]:
            raise ConnectorError("No model metadata was returned by Teacher discovery")

        report["status"] = "connector_ready"
        if not args.task.strip():
            report["knowledge"] = {
                "status": "not_requested",
                "note": "Supply --task or VORAX_X to ask a remote Teacher and propose one method.",
            }
        else:
            model_id = choose_teacher_model(discovery["models"], args.model_id)
            prompt = (
                "Teach VORAX how to perform the user's task as one bounded V-Lang v1 method. "
                "Return ONLY a single JSON object with keys version,id,domain,task,triggers,inputs,steps,"
                "result,tests,provenance. Include at least two executable tests with exact expected "
                "outputs. Use only the approved V-Lang operations or a registered CALL TOOL contract. "
                "Do not invent tool outputs or claim media has been generated. No code, shell, URLs, "
                "weights, secrets, or model configuration. The trusted VORAX Runtime will revalidate "
                "every test. Do not reproduce an existing method if the Home index already covers it.\n"
                "User X:\n" + args.task.strip()[:12000]
            )
            _, teacher_result = request_json(
                TEACHER_URL,
                "/teacher/ask",
                method="POST",
                payload={"prompt": prompt, "model_id": model_id},
                child_key=child_key,
                timeout=120,
            )
            if teacher_result.get("remote_inference") is not True:
                raise ConnectorError("Teacher did not confirm remote Inference Provider execution")
            if teacher_result.get("kaggle_t4_used") is not False:
                raise ConnectorError("Teacher returned incorrect Kaggle GPU provenance")
            if teacher_result.get("weights_downloaded") is not False:
                raise ConnectorError("Teacher reported a model weight download")
            candidate = _candidate_from_teacher(str(teacher_result.get("response", "")))
            duplicate = home_duplicate_reason(home_index, candidate)
            if duplicate:
                report["knowledge"] = {
                    "status": "already_in_home",
                    "reason": duplicate,
                    "method_id": candidate.get("id"),
                }
            else:
                status, runtime_result = request_json(
                    RUNTIME_URL,
                    "/vorax/verify",
                    method="POST",
                    payload={
                        "method": candidate,
                        "x": args.task.strip()[:12000],
                        "teacher_model_id": model_id,
                        "child_id": CHILD_ID,
                        "api_key": child_key,
                    },
                    child_key=child_key,
                    timeout=180,
                )
                report["knowledge"] = {
                    "status": runtime_result.get("status"),
                    "teacher_model_id": model_id,
                    "provider_used": teacher_result.get("provider_used"),
                    "remote_inference": teacher_result.get("remote_inference"),
                    "kaggle_t4_used": teacher_result.get("kaggle_t4_used"),
                    "method_id": runtime_result.get("method"),
                    "method_sha256": runtime_result.get("method_sha256"),
                    "verification": runtime_result.get("verification"),
                    "vault": runtime_result.get("vault"),
                    "home_submission": runtime_result.get("home_submission"),
                    "http_status": status,
                }
                if runtime_result.get("status") not in {"verified", "unknown", "blocked"}:
                    raise ConnectorError("Runtime returned an unsupported verification status")

        if runtime_health.get("ready") is not True:
            report["status"] = "connector_degraded"
            report["failure"] = "Runtime is reachable but health reports ready=false; inspect its Vault/Home readiness."
        return_code = 0 if report["status"] in {"connector_ready", "connector_degraded"} else 1

    except Exception as exc:
        report["status"] = "connector_blocked"
        report["failure"] = type(exc).__name__ + ": " + str(exc)[:400]
        return_code = 1
    finally:
        save_report(report)

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
