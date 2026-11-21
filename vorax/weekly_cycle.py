"""Pure weekly-cycle curriculum, manifest, report, retry and proposal-deduplication rules."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta, timezone

CYCLE_ID_RE = re.compile(r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")
COUNT_FIELDS = (
    "discovered_count",
    "understood_count",
    "acquired_count",
    "verified_count",
    "added_count",
    "rejected_count",
)
HUNTER_SLICES = (
    ("hunter-1", "text-docs"),
    ("hunter-2", "vision-audio"),
    ("hunter-3", "video-tools"),
)
TERMINAL_HUNTER_STATES = {"completed", "failed", "timed_out"}

# These are curriculum tasks, not generated code. The Teacher proposes a V-Lang
# procedure; the Runtime independently executes the required test vectors.
WEEKLY_CAPABILITY_CURRICULUM = (
    {
        "id": "business.gross_margin_percent",
        "domain": "business",
        "task": "calculate gross profit margin as a percentage of revenue",
        "triggers": ["gross margin", "gross profit margin", "margin percentage"],
        "inputs": {"revenue": "number", "cost": "number"},
        "tests": [
            {"input": {"revenue": 200, "cost": 120}, "expect": 40},
            {"input": {"revenue": 150, "cost": 90}, "expect": 40},
            {"input": {"revenue": 500, "cost": 350}, "expect": 30},
        ],
    },
    {
        "id": "business.markup_percent",
        "domain": "business",
        "task": "calculate markup percentage from cost and selling price",
        "triggers": ["markup percentage", "markup rate", "price markup"],
        "inputs": {"cost": "number", "selling_price": "number"},
        "tests": [
            {"input": {"cost": 100, "selling_price": 125}, "expect": 25},
            {"input": {"cost": 80, "selling_price": 100}, "expect": 25},
            {"input": {"cost": 200, "selling_price": 260}, "expect": 30},
        ],
    },
    {
        "id": "business.break_even_quantity",
        "domain": "business",
        "task": "calculate break-even units from fixed cost, selling price and variable cost per unit",
        "triggers": ["break even units", "break even quantity", "break even point"],
        "inputs": {"fixed_cost": "number", "selling_price": "number", "variable_cost": "number"},
        "tests": [
            {"input": {"fixed_cost": 1000, "selling_price": 50, "variable_cost": 30}, "expect": 50},
            {"input": {"fixed_cost": 1500, "selling_price": 70, "variable_cost": 40}, "expect": 50},
            {"input": {"fixed_cost": 1200, "selling_price": 60, "variable_cost": 30}, "expect": 40},
        ],
    },
    {
        "id": "geometry.rectangle_area",
        "domain": "geometry",
        "task": "calculate rectangle area from length and width",
        "triggers": ["rectangle area", "area of rectangle", "length times width"],
        "inputs": {"length": "number", "width": "number"},
        "tests": [
            {"input": {"length": 8, "width": 5}, "expect": 40},
            {"input": {"length": 12, "width": 3}, "expect": 36},
            {"input": {"length": 2.5, "width": 4}, "expect": 10},
        ],
    },
    {
        "id": "geometry.rectangle_perimeter",
        "domain": "geometry",
        "task": "calculate rectangle perimeter from length and width",
        "triggers": ["rectangle perimeter", "perimeter of rectangle", "twice length plus width"],
        "inputs": {"length": "number", "width": "number"},
        "tests": [
            {"input": {"length": 8, "width": 5}, "expect": 26},
            {"input": {"length": 12, "width": 3}, "expect": 30},
            {"input": {"length": 2.5, "width": 4}, "expect": 13},
        ],
    },
    {
        "id": "finance.tax_exclusive_amount",
        "domain": "finance",
        "task": "calculate the tax-exclusive amount from a tax-inclusive amount and tax percentage",
        "triggers": ["tax exclusive amount", "remove tax", "price before tax"],
        "inputs": {"gross_amount": "number", "tax_percent": "number"},
        "tests": [
            {"input": {"gross_amount": 116, "tax_percent": 16}, "expect": 100},
            {"input": {"gross_amount": 110, "tax_percent": 10}, "expect": 100},
            {"input": {"gross_amount": 232, "tax_percent": 16}, "expect": 200},
        ],
    },
    {
        "id": "finance.simple_interest",
        "domain": "finance",
        "task": "calculate simple interest from principal, annual rate percentage and time in years",
        "triggers": ["simple interest", "interest earned", "interest calculation"],
        "inputs": {"principal": "number", "annual_rate": "number", "years": "number"},
        "tests": [
            {"input": {"principal": 1000, "annual_rate": 5, "years": 2}, "expect": 100},
            {"input": {"principal": 2500, "annual_rate": 8, "years": 3}, "expect": 600},
            {"input": {"principal": 500, "annual_rate": 10, "years": 1}, "expect": 50},
        ],
    },
)


class WeeklyCycleError(ValueError):
    """Invalid or inconsistent weekly-cycle data."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    current = value or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def validate_cycle_id(cycle_id: str) -> str:
    if not isinstance(cycle_id, str) or not CYCLE_ID_RE.fullmatch(cycle_id):
        raise WeeklyCycleError("cycle_id must use ISO week format YYYY-Www")
    try:
        datetime.strptime(cycle_id + "-1", "%G-W%V-%u")
    except ValueError as exc:
        raise WeeklyCycleError("cycle_id is not a valid ISO week") from exc
    return cycle_id


def current_cycle_id(now: datetime | None = None) -> str:
    current = now or utc_now()
    iso = current.astimezone(timezone.utc).isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def _task_is_covered(task: dict, home_index: dict | None) -> bool:
    if not home_index:
        return False
    records = home_index.get("records", [])
    if not isinstance(records, list):
        raise WeeklyCycleError("Home metadata index records are malformed")
    task_triggers = {value.casefold() for value in task["triggers"]}
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("id") == task["id"]:
            return True
        if (
            str(record.get("domain", "")).casefold() == task["domain"].casefold()
            and str(record.get("task", "")).casefold() == task["task"].casefold()
        ):
            return True
        known_triggers = {
            str(value).casefold()
            for value in record.get("triggers", [])
            if isinstance(value, str)
        }
        if record.get("domain") == task["domain"] and task_triggers.intersection(known_triggers):
            return True
    return False


def select_weekly_curriculum_task(
    cycle_id: str,
    home_index: dict | None,
    pending_method_ids: set[str] | None = None,
) -> dict | None:
    """Select the next unlearned, deterministic task without touching an active Input X."""
    validate_cycle_id(cycle_id)
    week = int(cycle_id.split("-W", 1)[1])
    start = (week - 48) % len(WEEKLY_CAPABILITY_CURRICULUM)
    for offset in range(len(WEEKLY_CAPABILITY_CURRICULUM)):
        task = WEEKLY_CAPABILITY_CURRICULUM[(start + offset) % len(WEEKLY_CAPABILITY_CURRICULUM)]
        if task["id"] in (pending_method_ids or set()):
            continue
        if not _task_is_covered(task, home_index):
            return json.loads(json.dumps(task))
    return None


def validate_candidate_for_curriculum(candidate: dict, curriculum_task: dict) -> None:
    """Require exact task identity, input schema and every reference test vector."""
    if not isinstance(candidate, dict) or not isinstance(curriculum_task, dict):
        raise WeeklyCycleError("Candidate and curriculum task must be objects")
    for field in ("id", "domain", "task"):
        if candidate.get(field) != curriculum_task.get(field):
            raise WeeklyCycleError(f"Candidate {field} does not match the assigned curriculum task")
    if candidate.get("inputs") != curriculum_task.get("inputs"):
        raise WeeklyCycleError("Candidate input schema does not match the assigned curriculum task")
    candidate_tests = candidate.get("tests")
    if not isinstance(candidate_tests, list):
        raise WeeklyCycleError("Candidate tests are missing")
    canonical = {
        json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for item in candidate_tests
    }
    for required in curriculum_task.get("tests", []):
        marker = json.dumps(required, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if marker not in canonical:
            raise WeeklyCycleError("Candidate omitted a mandatory curriculum test vector")
    required_triggers = {value.casefold() for value in curriculum_task.get("triggers", [])}
    candidate_triggers = {
        value.casefold()
        for value in candidate.get("triggers", [])
        if isinstance(value, str)
    }
    if not required_triggers.issubset(candidate_triggers):
        raise WeeklyCycleError("Candidate omitted one or more assigned curriculum triggers")


def build_cycle_manifest(
    cycle_id: str,
    *,
    now: datetime | None = None,
    hunter_count: int = 3,
    home_issue_url: str | None = None,
    home_index: dict | None = None,
    pending_method_ids: set[str] | None = None,
) -> dict:
    validate_cycle_id(cycle_id)
    if isinstance(hunter_count, bool) or not isinstance(hunter_count, int) or not 1 <= hunter_count <= 3:
        raise WeeklyCycleError("hunter_count must be between 1 and 3")
    started = now or utc_now()
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    assignments = []
    for hunter_id, discovery_slice in HUNTER_SLICES[:hunter_count]:
        assignments.append({
            "hunter_id": hunter_id,
            "discovery_slice": discovery_slice,
            "status": "queued",
            "attempt": 0,
            "timeout_seconds": 7200,
            "deadline_at": timestamp(started + timedelta(hours=2)),
            "report_path": f"reports/weekly/hunters/{cycle_id}/{hunter_id}-attempt-0.json",
        })
    return {
        "version": "vorax-weekly-cycle/v1",
        "cycle_id": cycle_id,
        "status": "discovering",
        "created_at": timestamp(started),
        "deadline_at": timestamp(started + timedelta(days=7)),
        "retry_count": 0,
        "retry_after": None,
        "home_issue_url": home_issue_url,
        "hunter_assignments": assignments,
        "curriculum_task": select_weekly_curriculum_task(cycle_id, home_index, pending_method_ids),
        "report_path": f"reports/weekly/{cycle_id}.json",
        "counts": {key: 0 for key in COUNT_FIELDS},
        "missing_X_list": [],
        "next_priority": [],
        "weights_downloaded": False,
        "local_model_loaded": False,
        "billable_inference_allowed": False,
    }


def validate_hunter_report(report: dict, *, cycle_id: str, hunter_id: str, discovery_slice: str, attempt: int = 0) -> dict:
    validate_cycle_id(cycle_id)
    if not isinstance(report, dict):
        raise WeeklyCycleError("Hunter report must be an object")
    normalized = dict(report)
    for field in COUNT_FIELDS:
        value = normalized.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise WeeklyCycleError(f"{field} must be a non-negative integer")
        normalized[field] = value
    for field in ("missing_X_list", "next_priority"):
        value = normalized.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise WeeklyCycleError(f"{field} must be a list of non-empty strings")
        normalized[field] = list(dict.fromkeys(item.strip()[:240] for item in value))[:100]
    rejected = normalized.get("rejected", [])
    if not isinstance(rejected, list) or any(not isinstance(item, dict) for item in rejected):
        raise WeeklyCycleError("rejected must be a list of reason objects")
    normalized["rejected"] = rejected[:100]
    state = normalized.get("status", "completed")
    if state not in {"completed", "failed"}:
        raise WeeklyCycleError("Hunter report status must be completed or failed")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt not in (0, 1):
        raise WeeklyCycleError("Hunter attempt must be 0 or 1")
    normalized["status"] = state
    normalized["attempt"] = attempt
    normalized["cycle_id"] = cycle_id
    normalized["hunter_id"] = hunter_id
    normalized["discovery_slice"] = discovery_slice
    normalized["weights_downloaded"] = False
    normalized["local_model_loaded"] = False
    normalized["billable_inference_allowed"] = False
    normalized["reported_at"] = normalized.get("reported_at") or timestamp()
    return normalized


def update_hunter_assignment(
    manifest: dict,
    hunter_id: str,
    *,
    status: str,
    report: dict | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> dict:
    allowed = {"queued", "running", "completed", "failed", "timed_out"}
    if status not in allowed:
        raise WeeklyCycleError("Unsupported hunter status")
    updated = json.loads(json.dumps(manifest))
    assignments = updated.get("hunter_assignments")
    if not isinstance(assignments, list):
        raise WeeklyCycleError("Cycle manifest has no hunter assignments")
    assignment = next((item for item in assignments if item.get("hunter_id") == hunter_id), None)
    if assignment is None:
        raise WeeklyCycleError("Hunter is not assigned to this cycle")
    assignment["status"] = status
    assignment["updated_at"] = timestamp(now)
    if reason:
        assignment["reason"] = str(reason)[:240]
    if report is not None:
        assignment["report_sha256"] = hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    states = [item.get("status") for item in assignments]
    if all(state == "completed" for state in states):
        updated["status"] = "complete"
        updated["retry_after"] = None
    elif all(state in TERMINAL_HUNTER_STATES for state in states):
        if int(updated.get("retry_count", 0)) < 1:
            retry_at = (now or utc_now()) + timedelta(hours=24)
            updated["status"] = "retry_scheduled"
            updated["retry_after"] = timestamp(retry_at)
        else:
            updated["status"] = "failed"
            updated["retry_after"] = None
            updated["failure_reason"] = "Hunter mesh remained incomplete after one retry"
    elif updated.get("status") not in {"paused_teacher"}:
        updated["status"] = "discovering"
    return updated


def prepare_cycle_retry(manifest: dict, *, now: datetime | None = None) -> dict:
    updated = json.loads(json.dumps(manifest))
    if updated.get("status") != "retry_scheduled":
        return updated
    retry_after = updated.get("retry_after")
    if not isinstance(retry_after, str):
        raise WeeklyCycleError("Retry-scheduled cycle has no retry_after timestamp")
    due = datetime.fromisoformat(retry_after.replace("Z", "+00:00"))
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if current < due:
        return updated
    if int(updated.get("retry_count", 0)) >= 1:
        updated["status"] = "failed"
        return updated
    updated["retry_count"] = 1
    updated["retry_after"] = None
    updated["status"] = "discovering"
    for assignment in updated.get("hunter_assignments", []):
        if assignment.get("status") in {"failed", "timed_out"}:
            assignment["status"] = "queued"
            assignment["attempt"] = int(assignment.get("attempt", 0)) + 1
            assignment.pop("reason", None)
            assignment["deadline_at"] = timestamp(current + timedelta(hours=2))
            assignment["report_path"] = (
                f"reports/weekly/hunters/{updated['cycle_id']}/"
                f"{assignment['hunter_id']}-attempt-{assignment['attempt']}.json"
            )
    return updated


def aggregate_hunter_reports(manifest: dict, reports: list[dict]) -> dict:
    totals = {key: 0 for key in COUNT_FIELDS}
    missing: list[str] = []
    priorities: list[str] = []
    rejected: list[dict] = []
    for report in reports:
        for field in COUNT_FIELDS:
            totals[field] += int(report.get(field, 0))
        missing.extend(report.get("missing_X_list", []))
        priorities.extend(report.get("next_priority", []))
        rejected.extend(report.get("rejected", []))
    return {
        "version": "vorax-weekly-report/v1",
        "cycle_id": manifest["cycle_id"],
        "status": manifest.get("status", "complete"),
        **totals,
        "rejected": rejected[:500],
        "missing_X_list": list(dict.fromkeys(missing))[:500],
        "next_priority": list(dict.fromkeys(priorities))[:100],
        "hunter_count": len(manifest.get("hunter_assignments", [])),
        "hunters": [
            {
                "hunter_id": item["hunter_id"],
                "discovery_slice": item["discovery_slice"],
                "status": item["status"],
                "attempt": item.get("attempt", 0),
                "report_path": item.get("report_path"),
            }
            for item in manifest.get("hunter_assignments", [])
        ],
        "weights_downloaded": False,
        "local_model_loaded": False,
        "billable_inference_allowed": False,
        "created_at": timestamp(),
        "vault_path": f"reports/weekly/{manifest['cycle_id']}.json",
        "home_report_requires_pull_request": True,
    }


def proposal_digest_key(method_sha: str, cycle_id: str) -> str:
    validate_cycle_id(cycle_id)
    if not isinstance(method_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", method_sha):
        raise WeeklyCycleError("method_sha must be a lowercase SHA-256 digest")
    return f"{method_sha}:{cycle_id}"


class ProposalDeduplicator:
    """In-process testable equivalent of the Runtime's persistent Vault digest ledger."""
    def __init__(self) -> None:
        self._claims: dict[str, str] = {}
        self._lock = threading.Lock()

    def claim(self, method_sha: str, cycle_id: str, hunter_id: str) -> dict:
        key = proposal_digest_key(method_sha, cycle_id)
        if not isinstance(hunter_id, str) or not hunter_id:
            raise WeeklyCycleError("hunter_id is required")
        with self._lock:
            existing = self._claims.get(key)
            if existing is None:
                self._claims[key] = hunter_id
                return {"claimed": True, "duplicate": False, "idempotent_retry": False}
            if existing == hunter_id:
                return {"claimed": True, "duplicate": False, "idempotent_retry": True}
            return {
                "claimed": False,
                "duplicate": True,
                "idempotent_retry": False,
                "first_hunter_id": existing,
            }
