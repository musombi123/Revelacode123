"""Metadata-only index for Home's V-Lang library.

Children may read these summaries to avoid duplicate learning. This module never
exports executable V-Lang method bodies from Home into a Child runtime.
"""
from __future__ import annotations

import json
from typing import Iterable

from .vlang import fingerprint, validate_template

INDEX_VERSION = "vorax-home-method-index/v1"


def summarize_method(record: dict) -> dict:
    validate_template(record)
    return {
        "id": record["id"],
        "domain": record["domain"],
        "task": record["task"],
        "triggers": list(record["triggers"]),
        "fingerprint": fingerprint(record),
    }


def build_method_index(records: Iterable[dict]) -> dict:
    summaries = [summarize_method(record) for record in records]
    summaries.sort(key=lambda item: item["id"])
    ids = [item["id"] for item in summaries]
    if len(ids) != len(set(ids)):
        raise ValueError("Home method index contains duplicate method IDs")
    return {
        "version": INDEX_VERSION,
        "methods_total": len(summaries),
        "records": summaries,
    }


def parse_method_index(raw: str | bytes) -> dict:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Home method index is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("version") != INDEX_VERSION:
        raise ValueError("Unsupported Home method index version")
    records = value.get("records")
    if not isinstance(records, list) or value.get("methods_total") != len(records):
        raise ValueError("Home method index has an invalid record list")
    required = {"id", "domain", "task", "triggers", "fingerprint"}
    ids = set()
    for item in records:
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("Home method index entries must contain metadata only")
        if not isinstance(item.get("id"), str) or not isinstance(item.get("domain"), str) or not isinstance(item.get("task"), str):
            raise ValueError("Home method index entry has invalid text metadata")
        if not isinstance(item.get("triggers"), list) or any(not isinstance(x, str) for x in item["triggers"]):
            raise ValueError("Home method index triggers are invalid")
        if not isinstance(item.get("fingerprint"), str) or len(item["fingerprint"]) != 64:
            raise ValueError("Home method index fingerprint is invalid")
        if item["id"] in ids:
            raise ValueError("Home method index contains a duplicate ID")
        ids.add(item["id"])
    if records != sorted(records, key=lambda item: item["id"]):
        raise ValueError("Home method index records must be sorted by method ID")
    return value


def find_duplicate(index: dict, candidate: dict) -> str | None:
    """Return the duplicate reason using metadata only."""
    records = index.get("records", [])
    candidate_id = candidate.get("id")
    candidate_fingerprint = fingerprint(candidate)
    for item in records:
        if item.get("id") == candidate_id:
            return "method_id_exists_in_home"
        if item.get("fingerprint") == candidate_fingerprint:
            return "method_fingerprint_exists_in_home"
        if (
            str(item.get("domain", "")).casefold() == str(candidate.get("domain", "")).casefold()
            and str(item.get("task", "")).casefold() == str(candidate.get("task", "")).casefold()
        ):
            return "method_task_exists_in_home"
    return None


def append_method(index: dict, candidate: dict) -> dict:
    current = parse_method_index(json.dumps(index))
    reason = find_duplicate(current, candidate)
    if reason:
        raise ValueError(reason)
    records = list(current["records"])
    records.append(summarize_method(candidate))
    records.sort(key=lambda item: item["id"])
    return {"version": INDEX_VERSION, "methods_total": len(records), "records": records}
