from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .child import load_child_from_env
from .teacher import OpenAICompatibleTeacher, TeacherError
from .tlang import Composer, TLangVerifier, find_template

ROOT = Path(os.getenv("LEGEND_ROOT", str(Path(__file__).resolve().parent.parent)))
SEED_FILE = ROOT / "library" / "seeds.json"
TEMPLATE_DIR = ROOT / "library" / "templates"
app = FastAPI(title="RevelaCode Legend Child Engine", version="0.1.0", description="Deterministic T-Lang composition service; not a foundation model.")


class ComposeRequest(BaseModel):
    template_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class LearnRequest(BaseModel):
    task: str


class SubmitRequest(BaseModel):
    template_id: str


class SyncRequest(BaseModel):
    repository: str | None = None
    branch: str | None = None


def load_library() -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    if TEMPLATE_DIR.exists():
        for path in sorted(TEMPLATE_DIR.glob("*.json")): templates.append(json.loads(path.read_text(encoding="utf-8")))
    templates.extend(load_child_from_env().local_templates())
    # Keep first occurrence; Home and child sync are responsible for rejecting collisions.
    return list({item["id"]: item for item in reversed(templates)}.values())


def _authorized(authorization: str | None) -> None:
    expected = os.getenv("LEGEND_CHILD_API_TOKEN", "")
    if expected and not secrets.compare_digest((authorization or "").removeprefix("Bearer "), expected):
        raise HTTPException(status_code=401, detail="Invalid child API token")


@app.get("/v1/health")
def health() -> dict[str, Any]:
    items = load_library()
    return {
        "service": "revelacode-legend-child", "status": "ready", "runtime": "deterministic-tlang",
        "model_in_runtime": False, "teacher_configured": bool(os.getenv("LEGEND_TEACHER_API_KEY") and os.getenv("LEGEND_TEACHER_MODEL")),
        "template_count": len(items), "unknown_policy": "return UNKNOWN rather than inventing an answer",
        "limits": ["No general language understanding", "No native image or audio perception", "Finite test vectors do not prove universal truth"],
    }


@app.get("/v1/templates")
def templates() -> dict[str, Any]:
    items = load_library()
    return {"count": len(items), "items": [{"id": x["id"], "domain": x["domain"], "intent": x["intent"], "inputs": x["inputs"], "triggers": x.get("triggers", [])} for x in items]}


@app.post("/v1/compose")
def compose(request: ComposeRequest) -> dict[str, Any]:
    item = find_template(load_library(), request.template_id)
    if item is None:
        return {"status": "UNKNOWN", "template_id": request.template_id, "error": "No verified template matches this procedure.", "certainty": "not_established"}
    report = TLangVerifier().verify(item)
    if not report.passed:
        return {"status": "QUARANTINED", "template_id": request.template_id, "error": "Stored template failed current verification.", "verification": report.as_dict()}
    return Composer().run(item, request.inputs)


@app.post("/v1/learn")
def learn(request: LearnRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorized(authorization)
    try: candidate = OpenAICompatibleTeacher.from_env().extract(request.task)
    except TeacherError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    engine = load_child_from_env()
    try: result = engine.ingest(candidate, known_templates=load_library())
    except Exception as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result, "stored": "T-Lang only", "raw_teacher_response_stored": False, "verification_scope": "Finite test vectors and DSL safety checks; not proof of universal truth."}


@app.post("/v1/sync")
def sync(request: SyncRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorized(authorization)
    repository = request.repository or os.getenv("LEGEND_GITHUB_REPOSITORY", "")
    branch = request.branch or os.getenv("LEGEND_GITHUB_BASE_BRANCH", "main")
    if not repository: raise HTTPException(status_code=503, detail="Set LEGEND_GITHUB_REPOSITORY to enable Home sync.")
    try:
        return load_child_from_env().sync_from_github(repository, branch, os.getenv("LEGEND_HOME_READ_TOKEN", ""))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/submit")
def submit(request: SubmitRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorized(authorization)
    required = ["LEGEND_GITHUB_TOKEN", "LEGEND_GITHUB_REPOSITORY", "LEGEND_GITHUB_BASE_BRANCH", "LEGEND_CHILD_HMAC_SECRET"]
    missing = [name for name in required if not os.getenv(name)]
    if missing: raise HTTPException(status_code=503, detail="Missing child submission configuration: " + ", ".join(missing))
    try:
        return load_child_from_env().submit(request.template_id, os.environ["LEGEND_GITHUB_TOKEN"], os.environ["LEGEND_GITHUB_REPOSITORY"], os.environ["LEGEND_GITHUB_BASE_BRANCH"], os.environ["LEGEND_CHILD_HMAC_SECRET"])
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
