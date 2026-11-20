"""Small, local VORAX protocol primitives that are not copied from Home."""
from __future__ import annotations

import re

from .engine import find_template
from .vlang import TemplateError, execute, validate_template

IDENTITY_METHOD = {
    "version": "vlang/1",
    "id": "runtime.identity",
    "domain": "runtime",
    "task": "identify the VORAX runtime truthfully",
    "triggers": ["hello who are you", "identify VORAX runtime"],
    "inputs": {"query": "string"},
    "steps": [{
        "op": "identity",
        "args": [
            "Hello. I am VORAX — one Input X, one V-Lang and one shared verification runtime. "
            "I learn capabilities as verified methods; I am not a model and I do not claim work "
            "that has not been executed."
        ],
        "as": "answer",
    }],
    "result": {"step": "answer"},
    "tests": [
        {
            "input": {"query": "hello, who are you"},
            "expect": (
                "Hello. I am VORAX — one Input X, one V-Lang and one shared verification runtime. "
                "I learn capabilities as verified methods; I am not a model and I do not claim work "
                "that has not been executed."
            ),
        },
        {
            "input": {"query": "identify VORAX runtime"},
            "expect": (
                "Hello. I am VORAX — one Input X, one V-Lang and one shared verification runtime. "
                "I learn capabilities as verified methods; I am not a model and I do not claim work "
                "that has not been executed."
            ),
        },
    ],
    "provenance": {"kind": "seed"},
}

validate_template(IDENTITY_METHOD)


def handle_builtin_input(value: str) -> dict:
    """Answer only the runtime identity handshake; other knowledge must come from Away Children."""
    if not isinstance(value, str) or not value.strip() or len(value) > 12_000:
        return {"status": "blocked", "method": None, "answer": None, "reason": "invalid_input"}

    normalized = re.sub(r"[^a-z0-9 ]+", " ", value.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not re.search(r"\b(who are you|what are you|introduce yourself|identify vorax)\b", normalized):
        return {
            "status": "unknown",
            "method": None,
            "answer": None,
            "reason": "no_runtime_builtin_match",
            "learning_direction": "away_children_learn_from_world_and_propose_to_home",
        }

    answer, trace = execute(IDENTITY_METHOD, {"query": value})
    return {
        "status": "verified",
        "method": IDENTITY_METHOD["id"],
        "answer": answer,
        "verification": {
            "authority": "trusted_vlang_interpreter",
            "tests_passed": len(IDENTITY_METHOD["tests"]),
            "home_method_body_loaded": False,
            "child_self_certification": False,
        },
        "proof": trace,
    }

def execute_learned_input(value: str, catalog: list[dict], *, tool_executor=None) -> dict:
    """Execute only Home-approved methods that originated from a Child and were reconciled from Vault."""
    builtin = handle_builtin_input(value)
    if builtin.get("status") == "verified":
        return builtin
    if not isinstance(value, str) or not value.strip() or len(value) > 12_000:
        return {"status": "blocked", "method": None, "answer": None, "reason": "invalid_input"}

    template, reason = find_template(value, catalog)
    if template is None:
        return {
            "status": "unknown",
            "method": None,
            "answer": None,
            "reason": reason or "no_home_approved_child_method_matches",
            "home_method_bodies_loaded": False,
        }

    declared = template.get("inputs", {})
    if not isinstance(declared, dict) or len(declared) != 1:
        return {
            "status": "unknown",
            "method": template.get("id"),
            "answer": None,
            "reason": "input_mapping_required_for_multi_input_method",
        }

    name, kind = next(iter(declared.items()))
    original = value.strip()
    normalized = re.sub(r"[^a-z0-9 ]+", " ", original.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    phrases = [template.get("task", ""), *template.get("triggers", [])]
    remainder = original
    for phrase in phrases:
        phrase_norm = re.sub(r"[^a-z0-9 ]+", " ", str(phrase).casefold())
        phrase_norm = re.sub(r"\s+", " ", phrase_norm).strip()
        if phrase_norm and normalized.startswith(phrase_norm):
            remainder = original[len(str(phrase)):].strip(" \t:,-")
            break
    if not remainder:
        remainder = original

    try:
        if kind == "string":
            input_value = remainder
        elif kind == "integer":
            match = re.search(r"[-+]?\d+", remainder)
            if not match:
                raise ValueError("integer_input_missing")
            input_value = int(match.group(0))
        elif kind == "number":
            match = re.search(r"[-+]?\d+(?:\.\d+)?", remainder)
            if not match:
                raise ValueError("number_input_missing")
            input_value = float(match.group(0))
        elif kind == "number_list":
            numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", remainder)
            if not numbers:
                raise ValueError("number_list_input_missing")
            input_value = [float(number) for number in numbers]
        elif kind == "boolean":
            token = remainder.casefold().strip()
            if token in {"true", "yes", "on", "1"}:
                input_value = True
            elif token in {"false", "no", "off", "0"}:
                input_value = False
            else:
                raise ValueError("boolean_input_missing")
        else:
            raise ValueError("unsupported_input_type")
    except ValueError as exc:
        return {
            "status": "unknown",
            "method": template.get("id"),
            "answer": None,
            "reason": str(exc),
        }

    try:
        answer, trace = execute(
            template,
            {name: input_value},
            tool_executor=tool_executor,
        )
    except TemplateError as exc:
        return {
            "status": "unknown",
            "method": template.get("id"),
            "answer": None,
            "reason": "method_execution_unverified: " + str(exc)[:240],
        }

    artifact_sha256 = None
    artifact = answer if isinstance(answer, dict) else None
    if artifact:
        digest = artifact.get("sha256")
        if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest):
            artifact_sha256 = digest

    return {
        "status": "verified",
        "method": template["id"],
        "answer": answer,
        "artifact_sha256": artifact_sha256,
        "verification": {
            "authority": "trusted_vlang_interpreter",
            "home_approved": True,
            "tests_passed": len(template["tests"]),
            "child_self_certification": False,
        },
        "proof": trace,
    }

