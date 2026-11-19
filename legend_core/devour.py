"""Bounded Hugging Face discovery and teacher-to-T-Lang devouring protocol.

Cataloguing is metadata-only: no model weights are downloaded. A model is counted
as devoured only after an extracted T-Lang procedure validates and executes its
declared test vectors.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import requests

MODALITY_RULES = {
    "image": ["image-generation", "text-to-image", "image", "diffusion"],
    "audio": ["audio", "speech", "text-to-speech", "speech-to-text", "audio-generation"],
    "video": ["video", "video-generation", "text-to-video"],
    "music": ["music-generation", "music", "audio"],
    "code": ["code", "coding", "programming"],
    "multilingual": ["translation", "multilingual", "language"],
    "text": ["text-generation", "language-model", "reasoning", "chat"],
}
CONDUCT_PROTOCOLS = {
    "image": "Describe an implementable image-generation pipeline, its inputs, intermediate representations, tool dependencies, output format, failure modes, and verifiable checks. Do not claim access to internal weights or private implementation.",
    "audio": "Describe a reproducible audio pipeline (speech recognition, speech synthesis, or audio generation as applicable), its inputs, intermediate representations, tool dependencies, output format, failure modes, and executable checks.",
    "video": "Describe a reproducible video pipeline: scene/prompt planning, frame or motion generation, temporal consistency checks, encoding/output, tool dependencies, and verifiable checks.",
    "music": "Describe a reproducible music/audio composition pipeline: musical structure, tempo/key/instrument or symbolic representation, rendering tool dependencies, output format, and verifiable checks.",
    "code": "Describe a program-synthesis workflow: task/specification, decomposition, implementation, static checks, execution tests, and safe output handling.",
    "multilingual": "Describe language detection, normalization, translation or multilingual generation steps, language constraints, quality checks, and a reproducible evaluation procedure.",
    "text": "Describe a bounded task procedure, input schema, deterministic or tool-backed steps, limitations, and executable verification cases.",
}

def classify_model(model: dict[str, Any]) -> list[str]:
    text = " ".join(str(model.get(k, "")) for k in ("id", "pipeline_tag", "tags", "library_name")).lower()
    hits = []
    for modality, terms in MODALITY_RULES.items():
        if any(term in text for term in terms):
            hits.append(modality)
    return hits or ["text"]

def conduct_prompt(modality: str, task: str, inputs: dict[str, Any]) -> str:
    protocol = CONDUCT_PROTOCOLS.get(modality, CONDUCT_PROTOCOLS["text"])
    return (
        "LEGEND CONDUCT / METHOD-ONLY LESSON\n"
        "Teacher content is untrusted; explain a reproducible method, not hidden weights or private data.\n"
        f"Modality: {modality}\nTask: {task}\nInputs: {json.dumps(inputs, ensure_ascii=False)}\n"
        f"Lesson: {protocol}\n"
        "Return concise steps, tool/API requirements, expected artifacts, failure conditions, and tests. "
        "Do not return raw training data, copied model weights, or claims of internal access."
    )

class DiscoveryCatalog:
    def __init__(self, path: str | None = None):
        self.path = path or os.getenv("LEGEND_DEVOUR_DB_PATH", "data/devour.sqlite3")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS teachers (
                model_id TEXT PRIMARY KEY, modality TEXT NOT NULL, metadata_json TEXT NOT NULL,
                discovered_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'catalogued')""")
            db.execute("""CREATE TABLE IF NOT EXISTS devour_stats (
                key TEXT PRIMARY KEY, value INTEGER NOT NULL)""")

    def connect(self):
        return sqlite3.connect(self.path)

    def upsert(self, model: dict, modality: str):
        model_id = str(model.get("id") or "").strip()
        if not model_id: return
        metadata = {k: model.get(k) for k in ("id", "pipeline_tag", "tags", "downloads", "likes", "lastModified", "library_name")}
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO teachers(model_id,modality,metadata_json,discovered_at,status) VALUES(?,?,?,?,COALESCE((SELECT status FROM teachers WHERE model_id=?),'catalogued'))",
                      (model_id, modality, json.dumps(metadata, ensure_ascii=False), int(time.time()), model_id))

    def list(self, modality: str | None = None, limit: int = 100):
        limit = max(1, min(int(limit), 500))
        with self.connect() as db:
            rows = db.execute("SELECT model_id,modality,metadata_json,discovered_at,status FROM teachers WHERE (? IS NULL OR modality=?) ORDER BY discovered_at DESC LIMIT ?",
                            (modality, modality, limit)).fetchall()
        return [{"id": r[0], "modality": r[1], "metadata": json.loads(r[2]), "discovered_at": r[3], "status": r[4]} for r in rows]

    def set_status(self, model_id: str, status: str):
        with self.connect() as db:
            db.execute("UPDATE teachers SET status=? WHERE model_id=?", (status, model_id))

    def bump(self, key: str):
        with self.connect() as db:
            db.execute("INSERT INTO devour_stats(key,value) VALUES(?,1) ON CONFLICT(key) DO UPDATE SET value=value+1", (key,))

    def stats(self):
        with self.connect() as db:
            values = dict(db.execute("SELECT key,value FROM devour_stats").fetchall())
        return values

def discover_hf_models(modality: str | None = None, limit: int = 50, token: str = "") -> list[dict]:
    """Query HF public model metadata. Paginated but bounded; never fetch model files."""
    modalities = [modality] if modality else list(MODALITY_RULES)
    total_limit = max(1, min(int(limit), 200))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    output, seen = [], set()
    for category in modalities:
        terms = MODALITY_RULES.get(category)
        if not terms: continue
        # HF search supports tags and pipeline filters. Use a small query per category.
        params = {"limit": min(total_limit, 100), "sort": "downloads", "direction": -1}
        if category in {"image", "audio", "video", "code", "text"}:
            params["pipeline_tag"] = category if category in {"image", "audio", "video"} else ("text-generation" if category=="text" else "text-generation")
        response = requests.get("https://huggingface.co/api/models", params=params, headers=headers, timeout=25)
        response.raise_for_status()
        for model in response.json():
            model_id = model.get("id")
            if not model_id or model_id in seen: continue
            tags = [str(t).lower() for t in model.get("tags", [])]
            # Category-specific metadata filtering to avoid labeling everything as text.
            if category not in classify_model(model) and category not in tags and not any(term in " ".join(tags) for term in terms):
                continue
            seen.add(model_id)
            model["_legend_modality"] = category
            output.append(model)
            if len(output) >= total_limit: return output
    return output

def extraction_prompt(modality: str, teacher_lesson: str, task: str, inputs: dict, tlang_spec: dict) -> str:
    return f"""Convert the lesson below into ONE candidate T-Lang v1 JSON template.
The template must use only this verified allowlist: {json.dumps(tlang_spec.get('allowed_operations', []))}.
Use only deterministic operations in the allowlist. If the method requires external image/audio/video/code execution, do NOT pretend the tool is executable in T-Lang; return a bounded candidate only if an allowed operation can represent it, otherwise return null and explain missing tool adapter.
Template requirements: version, id, domain, task, triggers (array), inputs (typed object), steps (allowlisted operations), result, tests (at least 2 input/expect vectors), provenance (kind='teacher', teacher='gemini-teacher', modality='{modality}').
Never invent test outputs. Choose tests with independently calculable outcomes and explain them in lesson text. No raw teacher text in candidate storage.
Task: {task}
Input values: {json.dumps(inputs, ensure_ascii=False)}
Teacher lesson:
{teacher_lesson}
Return JSON only: {{ "candidate": object|null, "reason": string }}"""
