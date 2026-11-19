"""RevelaCode Legend API: deterministic composition plus optional teacher adapters."""
from __future__ import annotations
import hmac, os, re, time, uuid
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from legend_core.engine import compose
from legend_core.github_home import GitHubHomeError, submit_template, sync_library
from legend_core.store import TemplateStore
from legend_core.teacher import propose_template, teacher_configured
from legend_core.tlang import TemplateError, parse_document, validate_template

ROOT=Path(__file__).resolve().parent
STORE=TemplateStore(os.getenv("LEGEND_DB_PATH","data/legend.sqlite3"))
HOME_REPO=os.getenv("LEGEND_HOME_REPO","musombi123/Revelacode123")
HOME_BRANCH=os.getenv("LEGEND_HOME_BRANCH","main")
CHILD_ID=os.getenv("LEGEND_CHILD_ID","")
CHILD_KEY=os.getenv("LEGEND_CHILD_API_KEY","")
ADMIN_KEY=os.getenv("LEGEND_ADMIN_API_KEY","")
app=FastAPI(title="RevelaCode Legend",version="0.1.0",description="A bounded, auditable T-Lang composition engine.")
origins=[x.strip() for x in os.getenv("LEGEND_CORS_ORIGINS","*").split(",") if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=["GET","POST","OPTIONS"],allow_headers=["*"])

def load_local_seeds():
    folder=ROOT/"templates"/"library"
    if not folder.exists(): return
    for path in folder.rglob("*.tlang"):
        for record in parse_document(path.read_text(encoding="utf-8")): STORE.add(record)
load_local_seeds()

class ComposeRequest(BaseModel):
    template_id: str|None=None
    task: str|None=None
    inputs: dict=Field(default_factory=dict)
class ImportRequest(BaseModel):
    template: dict
class TeacherRequest(BaseModel):
    task: str
    inputs: dict
    triggers: list[str]
class SubmitRequest(BaseModel):
    template_id: str

def actor(request: Request):
    admin=request.headers.get("x-legend-admin-key","")
    child_id=request.headers.get("x-child-id","")
    child_key=request.headers.get("x-child-key","")
    if ADMIN_KEY and hmac.compare_digest(admin,ADMIN_KEY): return "admin"
    if CHILD_ID and CHILD_KEY and child_id==CHILD_ID and hmac.compare_digest(child_key,CHILD_KEY):
        if STORE.child_blocked(CHILD_ID): raise HTTPException(403,"Child is blocked after 100 consecutive rejected proposals")
        return CHILD_ID
    raise HTTPException(403,"Learning and submission require admin or registered child credentials")

def save_candidate(candidate: dict,who: str):
    record=dict(candidate); provenance=dict(record.get("provenance",{})); provenance["child_id"]=who
    if who=="admin" and provenance.get("kind")=="seed": provenance["kind"]="manual"
    record["provenance"]=provenance
    try:
        validate_template(record); STORE.add(record)
    except Exception:
        if who!="admin":
            failures=STORE.record_failure(who)
            if failures>=100: raise HTTPException(403,"Child automatically blocked after 100 consecutive rejected proposals")
        raise
    if who!="admin": STORE.record_success(who)
    return record

@app.get("/v1/health")
def health():
    return {"status":"ok","engine":"tlang-symbolic/1","templates":len(STORE.all()),
      "teacher_configured":teacher_configured(),"home_repository":HOME_REPO,"model_required_for_composition":False}

@app.get("/v1/templates")
def templates():
    items=[]
    for t in STORE.all():
        items.append({"id":t["id"],"domain":t["domain"],"task":t["task"],"triggers":t["triggers"],
          "inputs":t["inputs"],"example_input":t["tests"][0]["input"],"example_output":t["tests"][0]["expect"],
          "provenance":t["provenance"]["kind"]})
    return {"count":len(items),"templates":items}

@app.post("/v1/compose")
def compose_route(body: ComposeRequest):
    return compose(STORE.all(),template_id=body.template_id,task=body.task,inputs=body.inputs)

@app.post("/v1/learn/import")
def learn_import(body: ImportRequest,request: Request):
    who=actor(request)
    try: record=save_candidate(body.template,who)
    except (TemplateError,ValueError) as exc: raise HTTPException(422,str(exc)) from exc
    return {"status":"verified_and_stored","template_id":record["id"],"raw_teacher_text_stored":False,"template_count":len(STORE.all())}

@app.post("/v1/learn/teacher")
def learn_teacher(body: TeacherRequest,request: Request):
    who=actor(request)
    try: record=save_candidate(propose_template(body.task,body.inputs,body.triggers),who)
    except RuntimeError as exc:
        if "Teacher is not configured" in str(exc): raise HTTPException(503,str(exc))
        raise HTTPException(422,str(exc)) from exc
    except (TemplateError,ValueError) as exc: raise HTTPException(422,str(exc)) from exc
    return {"status":"teacher_proposal_verified_and_stored","template_id":record["id"],
            "teacher":"openai-compatible","raw_teacher_text_stored":False}

@app.post("/v1/sync")
def sync(request: Request):
    actor(request)
    try: remote=sync_library(HOME_REPO,HOME_BRANCH)
    except GitHubHomeError as exc: raise HTTPException(502,str(exc)) from exc
    added=0
    try:
        for record in remote:
            before=STORE.get(record["id"]); STORE.add(record)
            if before is None: added+=1
    except ValueError as exc: raise HTTPException(409,f"Home catalog conflict: {exc}") from exc
    return {"status":"synced","home":f"{HOME_REPO}@{HOME_BRANCH}","remote_templates":len(remote),
            "new_local_templates":added,"local_total":len(STORE.all())}

@app.post("/v1/submit")
def submit(body: SubmitRequest,request: Request):
    who=actor(request); record=STORE.get(body.template_id)
    if record is None: raise HTTPException(404,"Template not found")
    if record["provenance"]["kind"]=="seed": raise HTTPException(409,"Seed templates are already in Home")
    child_id=who if who!="admin" else record["provenance"].get("child_id","admin")
    try: result=submit_template(HOME_REPO,HOME_BRANCH,child_id,record,os.getenv("LEGEND_GITHUB_TOKEN",""))
    except (GitHubHomeError,ValueError) as exc: raise HTTPException(502,str(exc)) from exc
    return {"status":"submitted_for_home_verification",**result}


def _answer_text(text: str) -> dict:
    """Small, deterministic text bridge. It parses only explicit, supported numeric forms."""
    try:
        structured=__import__("json").loads(text)
        if isinstance(structured,dict) and "task" in structured:
            return compose(STORE.all(),task=str(structured["task"]),inputs=structured.get("inputs",{}))
    except (ValueError,TypeError):
        pass
    patterns=[
        (r"(-?\d+(?:\.\d+)?)\s*(?:\+|plus|add(?:ed)?(?:\s+to)?)\s*(-?\d+(?:\.\d+)?)","math.add",("a","b")),
        (r"(-?\d+(?:\.\d+)?)\s*(?:-|minus)\s*(-?\d+(?:\.\d+)?)","math.subtract",("a","b")),
        (r"(-?\d+(?:\.\d+)?)\s*(?:\*|x|times|multiplied by)\s*(-?\d+(?:\.\d+)?)","math.multiply",("a","b")),
        (r"(-?\d+(?:\.\d+)?)\s*(?:/|divided by)\s*(-?\d+(?:\.\d+)?)","math.divide",("a","b")),
    ]
    folded=text.casefold()
    for pattern,template_id,names in patterns:
        match=re.search(pattern,folded)
        if match:
            a,b=(float(value) for value in match.groups())
            a=int(a) if a.is_integer() else a; b=int(b) if b.is_integer() else b
            return compose(STORE.all(),template_id=template_id,inputs={names[0]:a,names[1]:b})
    percent=re.search(r"(-?\d+(?:\.\d+)?)\s*%\s*(?:of)\s*(-?\d+(?:\.\d+)?)",folded)
    if percent:
        p,v=(float(value) for value in percent.groups())
        return compose(STORE.all(),template_id="math.percentage_of",inputs={"percent":p,"value":v})
    return compose(STORE.all(),task=text,inputs={})

@app.get("/v1/models")
def model_catalog():
    return {"object":"list","data":[{"id":"legend-tlang-1","object":"model","owned_by":"revelacode",
      "capabilities":["verified-template-composition"],"weights":False}]}

@app.post("/v1/chat/completions")
def openai_compatible_chat(body: dict):
    """OpenAI-style chat endpoint for bounded template execution, not a language model."""
    messages=body.get("messages") or []
    user_message=next((item for item in reversed(messages) if item.get("role")=="user"),None)
    if not user_message: raise HTTPException(422,"At least one user message is required")
    text_value=user_message.get("content","")
    if not isinstance(text_value,str): raise HTTPException(422,"User message content must be text")
    answer=_answer_text(text_value)
    return {"id":"legend-"+uuid.uuid4().hex,"object":"chat.completion","created":int(time.time()),
      "model":"legend-tlang-1","choices":[{"index":0,
      "message":{"role":"assistant","content":__import__("json").dumps(answer,ensure_ascii=False)},
      "finish_reason":"stop"}]}

@app.post("/models/{model_id:path}")
def huggingface_style_inference(model_id: str,body: dict):
    """Hugging Face-style inference route for structured T-Lang tasks."""
    incoming=body.get("inputs")
    if isinstance(incoming,dict):
        task=incoming.get("task")
        values=incoming.get("inputs",{})
        if not isinstance(task,str) or not isinstance(values,dict):
            raise HTTPException(422,"inputs must contain task and an inputs object")
        answer=compose(STORE.all(),task=task,inputs=values)
    elif isinstance(incoming,str):
        answer=_answer_text(incoming)
    else:
        raise HTTPException(422,"inputs must be a task string or {task, inputs} object")
    return {"model":model_id,"result":answer}

@app.get("/v1/tlang/spec")
def tlang_spec():
    return {"version":"tlang/1","storage":"one compact JSON object per line in a .tlang file",
      "allowed_operations":["add","subtract","multiply","divide","percentage_of","percentage_change","average","minimum","maximum","sort_ascending","sort_descending","unique","count","word_count","uppercase","lowercase","reverse_text","celsius_to_fahrenheit","fahrenheit_to_celsius","km_to_miles","miles_to_km","kg_to_pounds","pounds_to_kg","implies","logical_and","logical_or","logical_not","absolute","round_number","greater_than","less_than","equals","identity"],
      "rule":"Every record requires executable test vectors; Home reruns them before merge."}
