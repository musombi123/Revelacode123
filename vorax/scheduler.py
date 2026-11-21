"""Weekly trigger policy and Runtime hand-off; learning never runs on GitHub Actions."""
from __future__ import annotations

from .network import CircuitBreaker, request_json
from .weekly_cycle import build_cycle_manifest, current_cycle_id, validate_cycle_id

WEEKLY_CRON = "0 2 * * 1"
RETRY_POLL_CRON = "0 3 * * *"


def build_weekly_payload(
    cycle_id: str | None = None,
    *,
    home_issue_url: str | None = None,
    hunter_count: int = 3,
) -> dict:
    selected = validate_cycle_id(cycle_id) if cycle_id else current_cycle_id()
    manifest = build_cycle_manifest(selected, hunter_count=hunter_count, home_issue_url=home_issue_url)
    return {
        "cycle_id": selected,
        "hunter_count": hunter_count,
        "home_issue_url": home_issue_url,
        "manifest_version": manifest["version"],
        "discovery_slices": [item["discovery_slice"] for item in manifest["hunter_assignments"]],
        "weights_downloaded": False,
        "billable_inference_allowed": False,
    }


def start_runtime_cycle(
    runtime_url: str,
    *,
    child_id: str,
    child_key: str,
    cycle_id: str | None = None,
    home_issue_url: str | None = None,
    breaker: CircuitBreaker | None = None,
) -> dict:
    if not runtime_url.startswith("https://"):
        raise ValueError("VORAX_RUNTIME_URL must use HTTPS")
    payload = build_weekly_payload(cycle_id, home_issue_url=home_issue_url)
    return request_json(
        runtime_url.rstrip("/") + "/vorax/cycle/start",
        method="POST",
        payload=payload,
        headers={"x-child-id": child_id, "x-child-key": child_key},
        timeout=30,
        attempts=3,
        breaker=breaker,
    )
