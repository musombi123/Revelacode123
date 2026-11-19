from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

TLANG_VERSION = "1.0"
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SECRET = re.compile(r"(?i)\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16})\b")
_FORBIDDEN_KEYS = {"password", "passwd", "secret", "api_key", "apikey", "access_token", "refresh_token", "authorization", "raw_output", "raw_response", "teacher_response", "completion_text", "private_key"}


class TLangError(ValueError):
    pass


@dataclass
class VerificationReport:
    passed: bool
    template_id: str
    issues: list[str] = field(default_factory=list)
    fingerprint: str | None = None
    tests_passed: int = 0
    tests_total: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "template_id": self.template_id, "issues": self.issues, "fingerprint": self.fingerprint, "tests_passed": self.tests_passed, "tests_total": self.tests_total}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def template_fingerprint(template: dict[str, Any]) -> str:
    material = {"domain": template.get("domain"), "inputs": template.get("inputs"), "steps": template.get("steps"), "output": template.get("output")}
    return hashlib.sha256(canonical_json(material)).hexdigest()


def find_template(templates: list[dict[str, Any]], template_id: str) -> dict[str, Any] | None:
    return next((item for item in templates if item.get("id") == template_id), None)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _text(value: Any, name: str = "value") -> str:
    if not isinstance(value, str):
        raise TLangError(name + " must be a string")
    return value


def _number(value: Any, name: str = "value") -> float | int:
    if not _is_number(value):
        raise TLangError(name + " must be a finite number")
    return value


def _list(value: Any, name: str = "value") -> list[Any]:
    if not isinstance(value, list):
        raise TLangError(name + " must be a list")
    return value


def _op(op: str, args: dict[str, Any]) -> Any:
    if op == "text.trim": return _text(args["value"]).strip()
    if op == "text.lower": return _text(args["value"]).lower()
    if op == "text.upper": return _text(args["value"]).upper()
    if op == "text.word_count": return len(_text(args["value"]).split())
    if op == "text.contains": return _text(args["needle"], "needle") in _text(args["value"])
    if op == "text.slug":
        value = _text(args["value"]).strip().lower()
        return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value)).strip("-")
    if op == "text.split":
        separator = _text(args["separator"], "separator")
        if not separator: raise TLangError("separator cannot be empty")
        return _text(args["value"]).split(separator)
    if op == "text.join":
        values = _list(args["values"], "values")
        if not all(isinstance(item, str) for item in values): raise TLangError("text.join values must all be strings")
        return _text(args["separator"], "separator").join(values)
    if op == "math.add":
        values = _list(args["values"], "values")
        return sum(_number(item, "values[]") for item in values)
    if op == "math.subtract": return _number(args["left"], "left") - _number(args["right"], "right")
    if op == "math.multiply":
        result: float | int = 1
        for item in _list(args["values"], "values"): result *= _number(item, "values[]")
        return result
    if op == "math.divide":
        left, right = _number(args["left"], "left"), _number(args["right"], "right")
        if right == 0: raise TLangError("division by zero is undefined")
        return left / right
    if op == "math.mean":
        values = [_number(item, "values[]") for item in _list(args["values"], "values")]
        if not values: raise TLangError("mean requires at least one value")
        return sum(values) / len(values)
    if op in {"math.min", "math.max"}:
        values = [_number(item, "values[]") for item in _list(args["values"], "values")]
        if not values: raise TLangError(op + " requires at least one value")
        return min(values) if op == "math.min" else max(values)
    if op == "math.round":
        digits = args.get("digits", 0)
        if not isinstance(digits, int) or isinstance(digits, bool) or not -12 <= digits <= 12: raise TLangError("digits must be an integer between -12 and 12")
        return round(_number(args["value"]), digits)
    if op == "math.clamp":
        value, lower, upper = _number(args["value"]), _number(args["lower"], "lower"), _number(args["upper"], "upper")
        if lower > upper: raise TLangError("lower must be less than or equal to upper")
        return min(max(value, lower), upper)
    if op == "math.modulo":
        left, right = _number(args["left"], "left"), _number(args["right"], "right")
        if right == 0: raise TLangError("modulo by zero is undefined")
        return left % right
    if op == "math.sqrt":
        value = _number(args["value"])
        if value < 0: raise TLangError("square root of a negative number is not a real result")
        return math.sqrt(value)
    if op == "list.unique":
        seen: set[bytes] = set()
        result = []
        for item in _list(args["values"], "values"):
            marker = canonical_json(item)
            if marker not in seen:
                seen.add(marker)
                result.append(item)
        return result
    if op == "list.sort_numbers":
        return sorted(_number(item, "values[]") for item in _list(args["values"], "values"))
    raise TLangError("unsupported operation: " + op)


_ALLOWED_OPS = {
    "text.trim", "text.lower", "text.upper", "text.word_count", "text.contains", "text.slug", "text.split", "text.join",
    "math.add", "math.subtract", "math.multiply", "math.divide", "math.mean", "math.min", "math.max", "math.round",
    "math.clamp", "math.modulo", "math.sqrt", "list.unique", "list.sort_numbers",
}
_REQUIRED_ARGS = {
    "text.trim": {"value"}, "text.lower": {"value"}, "text.upper": {"value"}, "text.word_count": {"value"},
    "text.contains": {"value", "needle"}, "text.slug": {"value"}, "text.split": {"value", "separator"},
    "text.join": {"values", "separator"}, "math.add": {"values"}, "math.subtract": {"left", "right"},
    "math.multiply": {"values"}, "math.divide": {"left", "right}, "math.mean": {"values"}, "math.min": {"values"},
    "math.max": {"values"}, "math.round": {"value"}, "math.clamp": {"value", "lower", "upper"},
    "math.modulo": {"left", "right"}, "math.sqrt": {"value"}, "list.unique": {"values"}, "list.sort_numbers": {"values"},
}


def _resolve(value: Any, inputs: dict[str, Any], step_values: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            ref = value["$ref"]
            if not isinstance(ref, str): raise TLangError("reference must be a string")
            if ref.startswith("input."):
                key = ref[6:]
                if key not in inputs: raise TLangError("missing input reference: " + ref)
                return inputs[key]
            if ref.startswith("step."):
                key = ref[5:]
                if key not in step_values: raise TLangError("unavailable step reference: " + ref)
                return step_values[key]
            raise TLangError("invalid reference: " + ref)
        return {key: _resolve(item, inputs, step_values) for key, item in value.items()}
    if isinstance(value, list): return [_resolve(item, inputs, step_values) for item in value]
    return value


class Composer:
    def execute(self, template: dict[str, Any], inputs: dict[str, Any]) -> Any:
        if not isinstance(inputs, dict): raise TLangError("inputs must be an object")
        definitions = template.get("inputs", {})
        unknown = sorted(set(inputs) - set(definitions))
        if unknown: raise TLangError("unexpected input(s): " + ", ".join(unknown))
        for name, definition in definitions.items():
            if definition.get("required", True) and name not in inputs: raise TLangError("missing required input: " + name)
            if name not in inputs: continue
            value, kind = inputs[name], definition.get("type")
            valid = {
                "string": isinstance(value, str), "number": _is_number(value),
                "integer": isinstance(value, int) and not isinstance(value, bool), "boolean": isinstance(value, bool),
                "array": isinstance(value, list), "object": isinstance(value, dict),
            }.get(kind, False)
            if not valid: raise TLangError("input " + name + " must have type " + str(kind))
        step_values: dict[str, Any] = {}
        for step in template.get("steps", []):
            args = _resolve(step["args"], inputs, step_values)
            step_values[step["id"]] = _op(step["op"], args)
        return _resolve(template["output"], inputs, step_values)

    def run(self, template: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        try:
            return {
                "status": "COMPLETED", "template_id": template["id"],
                "result": self.execute(template, inputs), "certainty": "verified_deterministic_path",
                "scope": "Produced by this named procedure; not a claim of general world knowledge.",
            }
        except (TLangError, KeyError, TypeError, ValueError, OverflowError) as exc:
            return {"status": "ERROR", "template_id": template.get("id"), "error": str(exc), "certainty": "not_established"}


class TLangVerifier:
    def __init__(self, max_bytes: int = 32768, max_steps: int = 64, max_tests: int = 100):
        self.max_bytes, self.max_steps, self.max_tests = max_bytes, max_steps, max_tests

    def verify(self, template: dict[str, Any], known_templates: list[dict[str, Any]] | None = None) -> VerificationReport:
        template_id = str(template.get("id", "<missing>")) if isinstance(template, dict) else "<invalid>"
        issues: list[str] = []
        if not isinstance(template, dict):
            return VerificationReport(False, template_id, ["template must be a JSON object"])
        try:
            size = len(canonical_json(template))
        except (TypeError, ValueError):
            return VerificationReport(False, template_id, ["template must contain JSON-compatible values"])
        if size > self.max_bytes: issues.append("template exceeds the 32 KiB size limit")
        self._check_privacy(template, issues)
        required = {"id", "version", "domain", "intent", "inputs", "steps", "output", "tests", "provenance"}
        missing, extra = sorted(required - set(template)), sorted(set(template) - (required | {"triggers", "submission"}))
        if missing: issues.append("missing required field(s): " + ", ".join(missing))
        if extra: issues.append("unknown top-level field(s): " + ", ".join(extra))
        if not _ID.fullmatch(str(template.get("id", ""))): issues.append("id must be a stable lowercase identifier")
        if template.get("version") != 1: issues.append("version must be 1")
        for name in ("domain", "intent"):
            value = template.get(name)
            if not isinstance(value, str) or not value.strip() or len(value) > 160: issues.append(name + " must be a non-empty string of at most 160 characters")
        inputs = template.get("inputs")
        if not isinstance(inputs, dict) or len(inputs) > 20:
            issues.append("inputs must be an object with at most 20 entries")
            inputs = {}
        for name, definition in inputs.items():
            if not isinstance(name, str) or not _ID.fullmatch(name.replace("_", ".")): issues.append("invalid input name: " + str(name))
            if not isinstance(definition, dict):
                issues.append("input definition must be an object: " + str(name))
                continue
            if definition.get("type") not in {"string", "number", "integer", "boolean", "array", "object"}: issues.append("unsupported input type for " + str(name))
            if "required" in definition and not isinstance(definition["required"], bool): issues.append("required must be boolean for " + str(name))
            if set(definition) - {"type", "required", "description"}: issues.append("unknown input property for " + str(name))
        steps = template.get("steps")
        if not isinstance(steps, list) or not steps or len(steps) > self.max_steps:
            issues.append("steps must contain between 1 and " + str(self.max_steps) + " operations")
            steps = []
        refs = {"input." + name for name in inputs}
        seen_step_ids: set[str] = set()
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or set(step) != {"id", "op", "args"}:
                issues.append("step " + str(index) + " must contain exactly id, op, and args")
                continue
            step_id, op, args = step.get("id"), step.get("op"), step.get("args")
            if not isinstance(step_id, str) or not _ID.fullmatch(step_id) or step_id in seen_step_ids: issues.append("step " + str(index) + " has an invalid or duplicate id")
            if op not in _ALLOWED_OPS: issues.append("step " + str(index) + " uses unsupported operation: " + str(op))
            elif not isinstance(args, dict):
                issues.append("step " + str(index) + " args must be an object")
            else:
                absent = _REQUIRED_ARGS[op] - set(args)
                if absent: issues.append("step " + str(index) + " missing argument(s): " + ", ".join(sorted(absent)))
                self._check_refs(args, refs, issues, "step " + str(index))
            if isinstance(step_id, str):
                seen_step_ids.add(step_id)
                refs.add("step." + step_id)
        self._check_refs(template.get("output"), refs, issues, "output")
        provenance = template.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("kind") not in {"seed", "teacher", "derived", "local"}:
            issues.append("provenance.kind must be seed, teacher, derived, or local")
        elif not isinstance(provenance.get("source_label"), str) or not provenance["source_label"].strip():
            issues.append("provenance.source_label is required")
        triggers = template.get("triggers", [])
        if not isinstance(triggers, list) or len(triggers) > 20 or not all(isinstance(x, str) and x.strip() for x in triggers):
            issues.append("triggers must be a list of at most 20 non-empty strings")
        tests = template.get("tests")
        if not isinstance(tests, list) or not 2 <= len(tests) <= self.max_tests:
            issues.append("tests must contain between 2 and " + str(self.max_tests) + " test vectors")
            tests = []
        fingerprint = template_fingerprint(template)
        for known in known_templates or []:
            if known.get("id") == template.get("id"):
                issues.append("template id already exists in the known library: " + str(template.get("id")))
                break
            if template_fingerprint(known) == fingerprint:
                issues.append("duplicate procedure: equivalent to " + str(known.get("id")))
                break
        if issues: return VerificationReport(False, template_id, issues, fingerprint, 0, len(tests))
        runner, passed = Composer(), 0
        for index, test in enumerate(tests):
            if not isinstance(test, dict) or set(test) != {"inputs", "expected"}:
                issues.append("test " + str(index) + " must contain exactly inputs and expected")
                continue
            try:
                actual = runner.execute(template, test["inputs"])
            except Exception as exc:
                issues.append("test " + str(index) + " execution failed: " + str(exc))
                continue
            if actual != test["expected"]:
                issues.append("test " + str(index) + " output does not match its expected value")
            else:
                passed += 1
        return VerificationReport(not issues and passed == len(tests), template_id, issues, fingerprint, passed, len(tests))

    def _check_refs(self, value: Any, available: set[str], issues: list[str], location: str) -> None:
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                ref = value["$ref"]
                if not isinstance(ref, str) or ref not in available: issues.append(location + " has unresolved or forward reference: " + str(ref))
                return
            for item in value.values(): self._check_refs(item, available, issues, location)
        elif isinstance(value, list):
            for item in value: self._check_refs(item, available, issues, location)

    def _check_privacy(self, value: Any, issues: list[str], path: str = "$") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in _FORBIDDEN_KEYS: issues.append("private or raw-output field is not allowed at " + path + "." + str(key))
                self._check_privacy(item, issues, path + "." + str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value): self._check_privacy(item, issues, path + "[" + str(index) + "]")
        elif isinstance(value, str):
            if _EMAIL.search(value): issues.append("email-like personal data detected at " + path)
            if _SECRET.search(value): issues.append("credential-like secret detected at " + path)
