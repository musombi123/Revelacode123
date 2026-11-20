"""Small, local VORAX protocol primitives that are not copied from Home."""
from __future__ import annotations

import re

from .vlang import execute, validate_template

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
