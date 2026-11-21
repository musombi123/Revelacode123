"""Pure weekly-cycle curriculum, manifest, report, retry and proposal-deduplication rules."""
from __future__ import annotations

import hashlib
import itertools
import json
import math
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


# Additional deterministic curriculum tasks. All vectors use the current V-Lang
# numeric input contract; every candidate is independently executed by Runtime.
WEEKLY_CAPABILITY_CURRICULUM += (
    {
        "id": "business.operating_profit",
        "domain": "business",
        "task": "calculate operating profit from revenue, cost of goods sold and operating expenses",
        "triggers": ["operating profit", "operating income", "profit after operating expenses"],
        "inputs": {"revenue": "number", "cost_of_goods": "number", "operating_expenses": "number"},
        "tests": [
            {"input": {"revenue": 200, "cost_of_goods": 120, "operating_expenses": 30}, "expect": 50},
            {"input": {"revenue": 400, "cost_of_goods": 250, "operating_expenses": 80}, "expect": 70},
            {"input": {"revenue": 1000, "cost_of_goods": 600, "operating_expenses": 150}, "expect": 250},
        ],
    },
    {
        "id": "business.net_profit_margin_percent",
        "domain": "business",
        "task": "calculate net profit margin as a percentage of revenue",
        "triggers": ["net profit margin", "net margin percentage", "profit margin percent"],
        "inputs": {"net_profit": "number", "revenue": "number"},
        "tests": [
            {"input": {"net_profit": 20, "revenue": 100}, "expect": 20},
            {"input": {"net_profit": 45, "revenue": 300}, "expect": 15},
            {"input": {"net_profit": -10, "revenue": 100}, "expect": -10},
        ],
    },
    {
        "id": "finance.current_ratio",
        "domain": "finance",
        "task": "calculate the current ratio from current assets and current liabilities",
        "triggers": ["current ratio", "current assets ratio", "liquidity current ratio"],
        "inputs": {"current_assets": "number", "current_liabilities": "number"},
        "tests": [
            {"input": {"current_assets": 1500, "current_liabilities": 750}, "expect": 2},
            {"input": {"current_assets": 2400, "current_liabilities": 1200}, "expect": 2},
            {"input": {"current_assets": 900, "current_liabilities": 600}, "expect": 1.5},
        ],
    },
    {
        "id": "finance.debt_to_equity_ratio",
        "domain": "finance",
        "task": "calculate debt to equity ratio from total debt and shareholders equity",
        "triggers": ["debt to equity", "debt equity ratio", "leverage ratio"],
        "inputs": {"total_debt": "number", "shareholders_equity": "number"},
        "tests": [
            {"input": {"total_debt": 500, "shareholders_equity": 1000}, "expect": 0.5},
            {"input": {"total_debt": 1200, "shareholders_equity": 800}, "expect": 1.5},
            {"input": {"total_debt": 300, "shareholders_equity": 600}, "expect": 0.5},
        ],
    },
    {
        "id": "finance.return_on_investment_percent",
        "domain": "finance",
        "task": "calculate return on investment percentage from initial investment and final value",
        "triggers": ["return on investment", "roi", "investment return percentage"],
        "inputs": {"initial_investment": "number", "final_value": "number"},
        "tests": [
            {"input": {"initial_investment": 100, "final_value": 125}, "expect": 25},
            {"input": {"initial_investment": 500, "final_value": 650}, "expect": 30},
            {"input": {"initial_investment": 1000, "final_value": 900}, "expect": -10},
        ],
    },
    {
        "id": "finance.percentage_change",
        "domain": "finance",
        "task": "calculate percentage change from an original value to a new value",
        "triggers": ["percentage change", "percent increase", "percent decrease"],
        "inputs": {"original_value": "number", "new_value": "number"},
        "tests": [
            {"input": {"original_value": 100, "new_value": 120}, "expect": 20},
            {"input": {"original_value": 80, "new_value": 60}, "expect": -25},
            {"input": {"original_value": 50, "new_value": 50}, "expect": 0},
        ],
    },
    {
        "id": "finance.unit_cost",
        "domain": "finance",
        "task": "calculate unit cost from total cost and quantity",
        "triggers": ["unit cost", "cost per unit", "average unit cost"],
        "inputs": {"total_cost": "number", "quantity": "number"},
        "tests": [
            {"input": {"total_cost": 100, "quantity": 5}, "expect": 20},
            {"input": {"total_cost": 1250, "quantity": 25}, "expect": 50},
            {"input": {"total_cost": 72, "quantity": 8}, "expect": 9},
        ],
    },
    {
        "id": "finance.sales_commission",
        "domain": "finance",
        "task": "calculate sales commission from sales amount and commission percentage",
        "triggers": ["sales commission", "commission amount", "commission calculation"],
        "inputs": {"sales_amount": "number", "commission_percent": "number"},
        "tests": [
            {"input": {"sales_amount": 1000, "commission_percent": 5}, "expect": 50},
            {"input": {"sales_amount": 2500, "commission_percent": 3}, "expect": 75},
            {"input": {"sales_amount": 800, "commission_percent": 10}, "expect": 80},
        ],
    },
    {
        "id": "finance.discount_amount",
        "domain": "finance",
        "task": "calculate discount amount from original price and discount percentage",
        "triggers": ["discount amount", "discount value", "price discount calculation"],
        "inputs": {"original_price": "number", "discount_percent": "number"},
        "tests": [
            {"input": {"original_price": 100, "discount_percent": 10}, "expect": 10},
            {"input": {"original_price": 250, "discount_percent": 20}, "expect": 50},
            {"input": {"original_price": 80, "discount_percent": 25}, "expect": 20},
        ],
    },
    {
        "id": "finance.tax_amount",
        "domain": "finance",
        "task": "calculate tax amount from a tax exclusive base and tax percentage",
        "triggers": ["tax amount", "tax value", "calculate tax on base amount"],
        "inputs": {"taxable_amount": "number", "tax_percent": "number"},
        "tests": [
            {"input": {"taxable_amount": 100, "tax_percent": 16}, "expect": 16},
            {"input": {"taxable_amount": 200, "tax_percent": 10}, "expect": 20},
            {"input": {"taxable_amount": 75, "tax_percent": 8}, "expect": 6},
        ],
    },
    {
        "id": "geometry.square_area",
        "domain": "geometry",
        "task": "calculate square area from its side length",
        "triggers": ["square area", "area of a square", "square surface area"],
        "inputs": {"side": "number"},
        "tests": [
            {"input": {"side": 4}, "expect": 16},
            {"input": {"side": 7}, "expect": 49},
            {"input": {"side": 2.5}, "expect": 6.25},
        ],
    },
    {
        "id": "geometry.triangle_area",
        "domain": "geometry",
        "task": "calculate triangle area from base and height",
        "triggers": ["triangle area", "area of a triangle", "half base times height"],
        "inputs": {"base": "number", "height": "number"},
        "tests": [
            {"input": {"base": 10, "height": 5}, "expect": 25},
            {"input": {"base": 8, "height": 6}, "expect": 24},
            {"input": {"base": 2.5, "height": 4}, "expect": 5},
        ],
    },
    {
        "id": "geometry.parallelogram_area",
        "domain": "geometry",
        "task": "calculate parallelogram area from base and perpendicular height",
        "triggers": ["parallelogram area", "area of a parallelogram", "base times height"],
        "inputs": {"base": "number", "height": "number"},
        "tests": [
            {"input": {"base": 10, "height": 4}, "expect": 40},
            {"input": {"base": 7, "height": 3}, "expect": 21},
            {"input": {"base": 2.5, "height": 8}, "expect": 20},
        ],
    },
    {
        "id": "geometry.trapezoid_area",
        "domain": "geometry",
        "task": "calculate trapezoid area from parallel sides and perpendicular height",
        "triggers": ["trapezoid area", "area of a trapezoid", "average parallel sides times height"],
        "inputs": {"parallel_side_a": "number", "parallel_side_b": "number", "height": "number"},
        "tests": [
            {"input": {"parallel_side_a": 8, "parallel_side_b": 4, "height": 5}, "expect": 30},
            {"input": {"parallel_side_a": 10, "parallel_side_b": 6, "height": 3}, "expect": 24},
            {"input": {"parallel_side_a": 5, "parallel_side_b": 3, "height": 4}, "expect": 16},
        ],
    },
    {
        "id": "math.weighted_average_two",
        "domain": "math",
        "task": "calculate a weighted average from two values and their weights",
        "triggers": ["weighted average", "weighted mean", "two value weighted average"],
        "inputs": {"value_a": "number", "weight_a": "number", "value_b": "number", "weight_b": "number"},
        "tests": [
            {"input": {"value_a": 80, "weight_a": 0.4, "value_b": 90, "weight_b": 0.6}, "expect": 86},
            {"input": {"value_a": 50, "weight_a": 0.25, "value_b": 70, "weight_b": 0.75}, "expect": 65},
            {"input": {"value_a": 100, "weight_a": 0.5, "value_b": 60, "weight_b": 0.5}, "expect": 80},
        ],
    },
    {
        "id": "math.average_three",
        "domain": "math",
        "task": "calculate arithmetic mean of three numeric values",
        "triggers": ["average of three", "mean of three numbers", "three value average"],
        "inputs": {"value_a": "number", "value_b": "number", "value_c": "number"},
        "tests": [
            {"input": {"value_a": 2, "value_b": 4, "value_c": 6}, "expect": 4},
            {"input": {"value_a": 10, "value_b": 15, "value_c": 20}, "expect": 15},
            {"input": {"value_a": -3, "value_b": 0, "value_c": 3}, "expect": 0},
        ],
    },
    {
        "id": "math.average_two",
        "domain": "math",
        "task": "calculate the arithmetic mean of two numeric values",
        "triggers": ["average of two", "mean of two numbers", "two value average"],
        "inputs": {"value_a": "number", "value_b": "number"},
        "tests": [
            {"input": {"value_a": 2, "value_b": 4}, "expect": 3},
            {"input": {"value_a": 10, "value_b": 20}, "expect": 15},
            {"input": {"value_a": -4, "value_b": 8}, "expect": 2},
        ],
    },
    {
        "id": "math.average_speed",
        "domain": "math",
        "task": "calculate average speed from distance and elapsed time",
        "triggers": ["average speed", "speed from distance and time", "distance per time"],
        "inputs": {"distance": "number", "elapsed_time": "number"},
        "tests": [
            {"input": {"distance": 100, "elapsed_time": 2}, "expect": 50},
            {"input": {"distance": 150, "elapsed_time": 3}, "expect": 50},
            {"input": {"distance": 42, "elapsed_time": 0.5}, "expect": 84},
        ],
    },
    {
        "id": "math.distance_from_speed_time",
        "domain": "math",
        "task": "calculate distance from average speed and elapsed time",
        "triggers": ["distance from speed and time", "speed times time", "travel distance"],
        "inputs": {"average_speed": "number", "elapsed_time": "number"},
        "tests": [
            {"input": {"average_speed": 50, "elapsed_time": 2}, "expect": 100},
            {"input": {"average_speed": 80, "elapsed_time": 1.5}, "expect": 120},
            {"input": {"average_speed": 12, "elapsed_time": 4}, "expect": 48},
        ],
    },
    {
        "id": "science.density",
        "domain": "science",
        "task": "calculate density from mass and volume",
        "triggers": ["density", "mass per volume", "calculate material density"],
        "inputs": {"mass": "number", "volume": "number"},
        "tests": [
            {"input": {"mass": 100, "volume": 20}, "expect": 5},
            {"input": {"mass": 75, "volume": 15}, "expect": 5},
            {"input": {"mass": 42, "volume": 7}, "expect": 6},
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


DYNAMIC_NUMERIC_OPERATORS = (
    "add",
    "subtract",
    "multiply",
    "divide",
    "percentage_of",
    "percentage_change",
)
DYNAMIC_NUMERIC_INPUTS = (
    "value_a", "value_b", "value_c", "value_d", "value_e", "value_f",
    "value_g", "value_h", "value_i", "value_j", "value_k", "value_l",
)
DYNAMIC_TEST_BASES = (
    (97, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37),
    (113, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41),
    (127, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43),
    (139, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47),
    (151, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53),
    (167, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59),
    (179, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61),
    (193, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67),
)


def _apply_curriculum_operator(operation: str, left: float, right: float) -> float | None:
    """Independent numeric oracle for the V-Lang arithmetic subset."""
    if operation == "add":
        result = left + right
    elif operation == "subtract":
        result = left - right
    elif operation == "multiply":
        result = left * right
    elif operation == "divide":
        if right == 0:
            return None
        result = left / right
    elif operation == "percentage_of":
        result = (left / 100.0) * right
    elif operation == "percentage_change":
        if left == 0:
            return None
        result = ((right - left) / abs(left)) * 100.0
    else:
        raise WeeklyCycleError("Dynamic curriculum selected an unsupported V-Lang operator")
    if not isinstance(result, (int, float)) or not math.isfinite(float(result)):
        return None
    return float(result)


def _dynamic_task_for_chain(cycle_id: str, chain: tuple[str, ...]) -> dict | None:
    """Build a finite-input, independently expected compound procedure specification."""
    validate_cycle_id(cycle_id)
    operation_labels = {
        "add": "add",
        "subtract": "sub",
        "multiply": "mul",
        "divide": "div",
        "percentage_of": "pct_of",
        "percentage_change": "pct_change",
    }
    chain_slug = "-".join(operation_labels[item] for item in chain)
    input_names = list(DYNAMIC_NUMERIC_INPUTS[: len(chain) + 1])
    tests = []
    for values in DYNAMIC_TEST_BASES:
        current = float(values[0])
        valid = True
        for index, operation in enumerate(chain):
            next_value = float(values[index + 1])
            result = _apply_curriculum_operator(operation, current, next_value)
            if result is None:
                valid = False
                break
            current = result
        if valid:
            tests.append({
                "input": {
                    input_names[index]: values[index]
                    for index in range(len(input_names))
                },
                "expect": current,
            })
        if len(tests) == 3:
            break
    if len(tests) < 3:
        return None
    iso_year, iso_week = cycle_id.split("-W")
    task = f"Evaluate numeric chain {chain_slug} in order."
    return {
        "id": f"math.weekly_{iso_year}_w{iso_week}",
        "domain": "math",
        "task": task,
        "triggers": [
            f"weekly numeric chain {iso_year}w{iso_week}",
            f"compound chain {chain_slug}"[:48],
        ],
        "inputs": {name: "number" for name in input_names},
        "operation_chain": list(chain),
        "ordered_inputs": input_names,
        "tests": tests,
        "curriculum_kind": "generated_deterministic_numeric_chain",
        "oracle": "independent_python_numeric_operator_reference",
    }


def _is_pending_equivalent(task: dict, pending_methods: list[dict] | None) -> bool:
    task_triggers = {value.casefold() for value in task["triggers"]}
    for pending in pending_methods or []:
        if not isinstance(pending, dict):
            continue
        same_domain = str(pending.get("domain", "")).casefold() == task["domain"].casefold()
        if pending.get("id") == task["id"]:
            return True
        if same_domain and str(pending.get("task", "")).casefold() == task["task"].casefold():
            return True
        pending_triggers = {
            str(value).casefold()
            for value in pending.get("triggers", [])
            if isinstance(value, str)
        }
        if same_domain and task_triggers.intersection(pending_triggers):
            return True
    return False


def _is_home_equivalent(task: dict, home_index: dict | None) -> bool:
    if not home_index:
        return False
    records = home_index.get("records", [])
    if not isinstance(records, list):
        raise WeeklyCycleError("Home metadata index records are malformed")
    return _task_is_covered(task, home_index)


def select_weekly_curriculum_task(
    cycle_id: str,
    home_index: dict | None,
    pending_method_ids: set[str] | None = None,
    pending_methods: list[dict] | None = None,
) -> dict | None:
    """Select a new task indefinitely: reviewed seeds first, then generated safe V-Lang chains."""
    validate_cycle_id(cycle_id)
    week = int(cycle_id.split("-W", 1)[1])
    start = (week - 48) % len(WEEKLY_CAPABILITY_CURRICULUM)
    pending_ids = pending_method_ids or set()
    for offset in range(len(WEEKLY_CAPABILITY_CURRICULUM)):
        task = WEEKLY_CAPABILITY_CURRICULUM[(start + offset) % len(WEEKLY_CAPABILITY_CURRICULUM)]
        if task["id"] in pending_ids or _is_pending_equivalent(task, pending_methods):
            continue
        if not _is_home_equivalent(task, home_index):
            return json.loads(json.dumps(task))

    # Exhaustive but bounded operation grammar. 2..11 operators over six safe
    # arithmetic primitives yields 435,356,460 distinct chains per curriculum pass.
    # When a chain is already in Home or Vault pending, advance to the next chain.
    serial = int(cycle_id[:4]) * 53 + week
    chain_count = sum(len(DYNAMIC_NUMERIC_OPERATORS) ** size for size in range(2, 12))
    chain_count_per_size = {
        size: len(DYNAMIC_NUMERIC_OPERATORS) ** size
        for size in range(2, 12)
    }
    initial = serial % chain_count

    for offset in range(chain_count):
        chain_index = (initial + offset) % chain_count
        remaining = chain_index
        chain_size = 2
        for size in range(2, 12):
            span = chain_count_per_size[size]
            if remaining < span:
                chain_size = size
                break
            remaining -= span
        digits = [0] * chain_size
        for position in range(chain_size - 1, -1, -1):
            digits[position] = remaining % len(DYNAMIC_NUMERIC_OPERATORS)
            remaining //= len(DYNAMIC_NUMERIC_OPERATORS)
        chain = tuple(DYNAMIC_NUMERIC_OPERATORS[index] for index in digits)
        task = _dynamic_task_for_chain(cycle_id, chain)
        if task is None or task["id"] in pending_ids:
            continue
        if _is_pending_equivalent(task, pending_methods) or _is_home_equivalent(task, home_index):
            continue
        return task
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

    required_chain = curriculum_task.get("operation_chain")
    if required_chain is not None:
        steps = candidate.get("steps")
        if (
            not isinstance(required_chain, list)
            or not isinstance(steps, list)
            or [item.get("op") for item in steps if isinstance(item, dict)] != required_chain
            or len(steps) != len(required_chain)
        ):
            raise WeeklyCycleError("Candidate V-Lang steps do not match the assigned generated operation chain")
        ordered_inputs = curriculum_task.get("ordered_inputs")
        if not isinstance(ordered_inputs, list) or len(ordered_inputs) != len(required_chain) + 1:
            raise WeeklyCycleError("Generated curriculum input ordering is malformed")
        for index, step in enumerate(steps):
            args = step.get("args") if isinstance(step, dict) else None
            expected_args = (
                [{"input": ordered_inputs[0]}, {"input": ordered_inputs[1]}]
                if index == 0
                else [{"step": steps[index - 1].get("as")}, {"input": ordered_inputs[index + 1]}]
            )
            if args != expected_args:
                raise WeeklyCycleError("Candidate changed generated curriculum argument ordering")


def build_cycle_manifest(
    cycle_id: str,
    *,
    now: datetime | None = None,
    hunter_count: int = 3,
    home_issue_url: str | None = None,
    home_index: dict | None = None,
    pending_method_ids: set[str] | None = None,
    pending_methods: list[dict] | None = None,
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
        "curriculum_task": select_weekly_curriculum_task(
            cycle_id, home_index, pending_method_ids, pending_methods
        ),
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
