from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

SYSTEM_INSTRUCTION = """You extract methods for RevelaCode Legend. Return exactly one JSON T-Lang v1 template, not an explanation. Only use these deterministic operations: text.trim, text.lower, text.upper, text.word_count, text.contains, text.slug, text.split, text.join, math.add, math.subtract, math.multiply, math.divide, math.mean, math.min, math.max, math.round, math.clamp, math.modulo, math.sqrt, list.unique, list.sort_numbers. Include id, version=1, domain, intent, triggers, inputs, steps, output, at least two independent tests, and provenance={kind:teacher,source_label:teacher-assisted method extraction}. References use {\"$ref\":\"input.name\"} or {\"$ref\":\"step.name\"}. Steps contain id, op, args. Do not include raw answers, personal information, credentials, copied private material, executable code, shell commands, imports, or arbitrary expressions. If not representable, return {\"error\":\"NOT_REPRESENTABLE\",\"reason\":\"short reason\"}. Finite test vectors do not prove universal truth."""


class TeacherError(RuntimeError):
    pass


class OpenAICompatibleTeacher:
    """Optional external teacher. It proposes methods; it is not the runtime engine."""

    def __init__(self, endpoint: str, api_key: str, model: str, timeout: int = 45):
        self.endpoint, self.api_key, self.model, self.timeout = endpoint, api_key, model, timeout

    @classmethod
    def from_env(cls) -> "OpenAICompatibleTeacher":
        endpoint = os.getenv("LEGEND_TEACHER_ENDPOINT", "https://api.openai.com/v1/chat/completions")
        api_key, model = os.getenv("LEGEND_TEACHER_API_KEY", ""), os.getenv("LEGEND_TEACHER_MODEL", "")
        if not api_key or not model:
            raise TeacherError("Set LEGEND_TEACHER_API_KEY and LEGEND_TEACHER_MODEL to enable teacher-assisted extraction.")
        return cls(endpoint, api_key, model)

    def extract(self, task: str) -> dict[str, Any]:
        if not task.strip(): raise TeacherError("task cannot be empty")
        payload = {"model": self.model, "temperature": 0, "messages": [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": "Translate this general method into an executable T-Lang template. Task: " + task},
        ]}
        request = urllib.request.Request(self.endpoint, data=json.dumps(payload).encode("utf-8"), headers={
            "Authorization": "Bearer " + self.api_key, "Content-Type": "application/json", "Accept": "application/json",
        }, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise TeacherError("teacher request failed with HTTP " + str(exc.code)) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TeacherError("teacher request failed: " + str(exc)) from exc
        try:
            content = response_data["choices"][0]["message"]["content"]
            candidate = json.loads(content) if isinstance(content, str) else None
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise TeacherError("teacher did not return a single valid T-Lang JSON object") from exc
        if not isinstance(candidate, dict): raise TeacherError("teacher proposal must be a JSON object")
        if candidate.get("error") == "NOT_REPRESENTABLE":
            raise TeacherError("method is not expressible in the current operator set: " + str(candidate.get("reason", "")))
        return candidate
