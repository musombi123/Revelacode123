"""REVELACODE VORAX API: universal Input X over verified V-Lang methods."""
from __future__ import annotations
import hmac, os, re, time, uuid, subprocess
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from vorax.engine import compose, find_template
from vorax.github_home import GitHubHomeError, submit_template, sync_library
from vorax.store import TemplateStore
from vorax.teacher import propose_template, teacher_configured
from vorax.vlang import TemplateError, parse_document, validate_template, execute
from vorax.devour import DiscoveryCatalog, discover_hf_models, conduct_prompt, extraction_prompt
from vorax.vault import mirror_home_and_artifacts, VaultError
from vorax.tools import CONTRACTS as TOOL_CONTRACTS, ARTIFACT_ROOT as TOOL_ARTIFACT_ROOT, registry as tool_registry_data, execute_tool_dict as execute_registered_tool
from vorax.input_x import classify_input_x, handle_input_x, discover_method_sources

ROOT=Path(__file__).resolve().parents[1]
STORE=TemplateStore(os.getenv("LEGEND_DB_PATH","data/legend.sqlite3"))
DEVOUR=DiscoveryCatalog()
HOME_REPO=os.getenv("LEGEND_HOME_REPO","musombi123/Revelacode123")
HOME_BRANCH=os.getenv("LEGEND_HOME_BRANCH","main")
CHILD_ID=os.getenv("VORAX_CHILD_ID",os.getenv("LEGEND_CHILD_ID",""))
CHILD_KEY=os.getenv("VORAX_CHILD_API_KEY",os.getenv("LEGEND_CHILD_API_KEY",""))
ADMIN_KEY=os.getenv("LEGEND_ADMIN_API_KEY","")
app=FastAPI(title="REVELACODE VORAX",version="0.2.0",description="One Input X; Home-first V-Lang execution with bounded registered tools.")
origins=[x.strip() for x in os.getenv("LEGEND_CORS_ORIGINS","*").split(",") if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=["GET","POST","OPTIONS"],allow_headers=["*"])

def load_local_seeds():
    folder=ROOT/"templates"/"library"
    if not folder.exists(): return
    for pattern in ("*.vlang","*.tlang"):
        for path in folder.rglob(pattern):
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

def gpu_telemetry():
    try:
        p=subprocess.run(["nvidia-smi","--query-gpu=name,memory.total,memory.used,utilization.gpu","--format=csv,noheader,nounits"],capture_output=True,text=True,timeout=3,check=True)
        rows=[x.strip() for x in p.stdout.splitlines() if x.strip()]
        return {"available":bool(rows),"source":"nvidia-smi","devices":rows}
    except Exception: return {"available":False,"source":"nvidia-smi","devices":[]}

@app.get("/health")
@app.get("/v1/health")
def health():
    return {"status":"ok","engine":"VORAX","templates":len(STORE.all()),"teacher_configured":teacher_configured(),
      "home_repository":HOME_REPO,"vault_configured":bool(os.getenv("HF_VAULT_TOKEN")),
      "vault_repository":os.getenv("HF_VAULT_REPO","musombi123/revelacode-vorax-vault"),
      "local_gpu":gpu_telemetry(),"model_required_for_composition":False}

@app.get("/v1/gpu")
def gpu_status():
    return {"status":"observed","local_gpu":gpu_telemetry(),
      "note":"Remote provider inference is not evidence of local Space GPU use."}

@app.post("/v1/vault/sync")
def vault_sync(request: Request):
    actor(request)
    try:
        records=sync_library(HOME_REPO,HOME_BRANCH,token=os.getenv("LEGEND_HOME_READ_TOKEN",""))
        return {"status":"mirrored","home_repository":HOME_REPO,**mirror_home_and_artifacts(records,TOOL_ARTIFACT_ROOT)}
    except VaultError as exc: raise HTTPException(503,str(exc)) from exc
    except Exception as exc: raise HTTPException(502,"Vault mirror failed: "+type(exc).__name__) from exc

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
    try: remote=sync_library(HOME_REPO,HOME_BRANCH,token=os.getenv("LEGEND_HOME_READ_TOKEN",""))
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
    # Bounded numeric extraction for matched procedures with declared numeric inputs.
    # It accepts exactly the number of values declared by the selected template.
    match,reason=find_template(text,STORE.all())
    if match:
        values=re.findall(r"(?<![a-z])[-+]?\d+(?:\.\d+)?(?![a-z])",folded)
        keys=list(match["inputs"])
        if len(values)==len(keys) and all(kind in {"number","integer"} for kind in match["inputs"].values()):
            parsed=[float(v) for v in values]
            parsed=[int(v) if v.is_integer() else v for v in parsed]
            return compose(STORE.all(),template_id=match["id"],inputs=dict(zip(keys,parsed)))
    percent=re.search(r"(-?\d+(?:\.\d+)?)\s*%\s*(?:of)\s*(-?\d+(?:\.\d+)?)",folded)
    if percent:
        p,v=(float(value) for value in percent.groups())
        return compose(STORE.all(),template_id="math.percentage_of",inputs={"percent":p,"value":v})
    return compose(STORE.all(),task=text,inputs={})

@app.post("/generate")
def model_generate(body: dict, model_id: str = "revelacode-vorax"):
    """Hugging Face-like model input/output envelope, backed only by T-Lang."""
    incoming=body.get("inputs")
    if isinstance(incoming,str):
        result=_answer_text(incoming)
    elif isinstance(incoming,dict):
        task=incoming.get("task")
        values=incoming.get("inputs",{})
        if not isinstance(task,str) or not isinstance(values,dict):
            raise HTTPException(422,"inputs object requires task string and optional inputs object")
        result=compose(STORE.all(),task=task,inputs=values)
    else:
        raise HTTPException(422,"inputs must be a string or {task, inputs} object")
    return {"answer":result.get("answer"),"template_used":result.get("template_id"),"verified":result.get("status")=="verified","status":result.get("status"),"message":result.get("message")}

@app.get("/v1/models")
def model_catalog():
    return {"object":"list","data":[{"id":"vorax-vlang-1","object":"model","owned_by":"revelacode",
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
    return {"id":"vorax-"+uuid.uuid4().hex,"object":"chat.completion","created":int(time.time()),
      "model":"vorax-vlang-1","choices":[{"index":0,
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



@app.post("/v1/devour/import")
def devour_import_candidate(body: dict, request: Request):
    who=actor(request)
    candidate=body.get("candidate")
    modality=str(body.get("modality","text")).lower()
    teacher_id=str(body.get("teacher_model_id","gemini-teacher"))
    if not isinstance(candidate,dict): raise HTTPException(422,"candidate must be a V-Lang object")
    if modality not in {"image","audio","video","music","code","multilingual","text"}: raise HTTPException(422,"unsupported modality")
    candidate["provenance"]={"kind":"teacher","teacher":"gemini-teacher","teacher_model_id":teacher_id,"modality":modality,"child_id":who}
    try:
        validate_template(candidate)
        for case in candidate.get("tests",[]):
            execute(candidate,case["input"])
        record=save_candidate(candidate,who)
    except Exception as exc:
        DEVOUR.bump("rejected")
        return {"status":"rejected_by_verifier","reason":str(exc)[:500],"raw_teacher_text_stored":False}
    DEVOUR.bump("verified")
    if body.get("submit_home",True):
        try:
            child_id=who if who!="admin" else record["provenance"].get("child_id","admin")
            result=submit_template(HOME_REPO,HOME_BRANCH,child_id,record,os.getenv("LEGEND_GITHUB_TOKEN",""))
            return {"status":"verified_and_submitted","template_id":record["id"],"home_submission":result,"raw_teacher_text_stored":False}
        except Exception as exc:
            return {"status":"verified_local_submission_failed","template_id":record["id"],"reason":type(exc).__name__,"raw_teacher_text_stored":False}
    return {"status":"verified_local","template_id":record["id"],"raw_teacher_text_stored":False}

@app.get("/v1/devour/catalog")
def devour_catalog(modality: str|None=None, limit: int=100):
    return {"catalogued":DEVOUR.list(modality,limit),"stats":DEVOUR.stats()}

@app.post("/v1/devour/discover")
def devour_discover(body: dict, request: Request):
    actor(request)
    modality=body.get("modality")
    if modality is not None and modality not in {"image","audio","video","music","code","multilingual","text"}:
        raise HTTPException(422,"unsupported modality")
    try:
        models=discover_hf_models(modality=modality,limit=body.get("limit",50),token=os.getenv("HF_READ_TOKEN",""))
    except Exception as exc:
        raise HTTPException(502,"Hugging Face discovery failed: "+type(exc).__name__) from exc
    for model in models:
        category=model.pop("_legend_modality",modality or "text")
        DEVOUR.upsert(model,category)
    return {"status":"catalogued_metadata_only","models_found":len(models),"models":models,"weights_downloaded":False}

@app.post("/v1/devour/conduct")
def devour_conduct(body: dict, request: Request):
    actor(request)
    modality=str(body.get("modality","text")).lower()
    task=str(body.get("task","")).strip()
    inputs=body.get("inputs") if isinstance(body.get("inputs"),dict) else {}
    if modality not in {"image","audio","video","music","code","multilingual","text"}:
        raise HTTPException(422,"unsupported modality")
    if not task or len(task)>500: raise HTTPException(422,"task must be 1–500 characters")
    return {"modality":modality,"task":task,"teacher_conduct_prompt":conduct_prompt(modality,task,inputs),"status":"conduct_prompt_ready","raw_teacher_content_stored":False}

@app.post("/v1/devour/learn")
def devour_learn(body: dict, request: Request):
    who=actor(request)
    modality=str(body.get("modality","text")).lower()
    task=str(body.get("task","")).strip()
    inputs=body.get("inputs") if isinstance(body.get("inputs"),dict) else {}
    triggers=body.get("triggers") if isinstance(body.get("triggers"),list) else []
    lesson=str(body.get("lesson","")).strip()
    model_id=str(body.get("teacher_model_id","teacher-unidentified"))
    if modality not in {"image","audio","video","music","code","multilingual","text"}:
        raise HTTPException(422,"unsupported modality")
    if not task or not lesson or len(task)>500 or len(lesson)>16000:
        raise HTTPException(422,"task and bounded lesson (max 16000 chars) are required")
    # This endpoint ingests an explicit teacher lesson. It never fetches model weights.
    # Extract only a testable symbolic candidate; opaque media tool adapters are rejected.
    spec={"allowed_operations":["add","subtract","multiply","divide","percentage_of","percentage_change","average","minimum","maximum","sort_ascending","sort_descending","unique","count","word_count","uppercase","lowercase","reverse_text","celsius_to_fahrenheit","fahrenheit_to_celsius","km_to_miles","miles_to_km","kg_to_pounds","pounds_to_kg","implies","logical_and","logical_or","logical_not","absolute","round_number","greater_than","less_than","equals","identity"]}
    base=os.getenv("LEGEND_TEACHER_BASE_URL","https://api.openai.com/v1").rstrip("/")
    key=os.getenv("LEGEND_TEACHER_API_KEY"); model=os.getenv("LEGEND_TEACHER_MODEL")
    if not key or not model: raise HTTPException(503,"Teacher API not configured; configure Gemini-compatible endpoint credentials.")
    prompt=extraction_prompt(modality,lesson,task,inputs,spec)
    payload={"model":model,"temperature":0,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":"You are a method extractor. Do not fabricate tests or tool execution. Return JSON only."},{"role":"user","content":prompt}]}
    import urllib.request
    req=urllib.request.Request(base+"/chat/completions",data=__import__("json").dumps(payload).encode(),headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=45) as res: teacher_payload=__import__("json").loads(res.read().decode())
        extracted=__import__("json").loads(teacher_payload["choices"][0]["message"]["content"])
    except Exception as exc: raise HTTPException(502,"Teacher method extraction failed: "+type(exc).__name__) from exc
    candidate=extracted.get("candidate")
    if not isinstance(candidate,dict): return {"status":"not_devoured","reason":extracted.get("reason","Teacher method requires unavailable tools or cannot be represented in T-Lang"),"modality":modality,"teacher_model_id":model_id,"raw_lesson_stored":False}
    candidate["provenance"]={"kind":"teacher","teacher":"gemini-teacher","teacher_model_id":model_id,"modality":modality,"child_id":who}
    try:
        validate_template(candidate)
        # Execute each declared test before accepting the candidate.
        for case in candidate.get("tests",[]):
            execute(candidate,case["input"])
        record=save_candidate(candidate,who)
    except Exception as exc:
        DEVOUR.bump("rejected")
        return {"status":"rejected_by_verifier","reason":str(exc)[:500],"modality":modality,"raw_lesson_stored":False}
    DEVOUR.bump("verified")
    DEVOUR.set_status(model_id,"method_learned")
    if body.get("submit_home",True):
        try:
            child_id=who if who!="admin" else record["provenance"].get("child_id","admin")
            submitted=submit_template(HOME_REPO,HOME_BRANCH,child_id,record,os.getenv("LEGEND_GITHUB_TOKEN",""))
            return {"status":"verified_and_submitted","template_id":record["id"],"modality":modality,"teacher_model_id":model_id,"home_submission":submitted,"raw_lesson_stored":False}
        except Exception as exc:
            return {"status":"verified_local_submission_failed","template_id":record["id"],"reason":type(exc).__name__,"raw_lesson_stored":False}
    return {"status":"verified_local","template_id":record["id"],"modality":modality,"raw_lesson_stored":False}

@app.get("/v1/devour/stats")
def devour_stats():
    return {"catalogued_models":len(DEVOUR.list(limit=500)),"devour":DEVOUR.stats(),"teacher_configured":teacher_configured(),"stored_templates":len(STORE.all())}

@app.get("/v1/tlang/spec")
@app.get("/v1/vlang/spec")
def vlang_spec():
    return {"version":"vlang/1","legacy_reader":"tlang/1","storage":"one compact JSON object per line in a .vlang file",
      "allowed_operations":["add","subtract","multiply","divide","percentage_of","percentage_change","average","minimum","maximum","sort_ascending","sort_descending","unique","count","word_count","uppercase","lowercase","reverse_text","celsius_to_fahrenheit","fahrenheit_to_celsius","km_to_miles","miles_to_km","kg_to_pounds","pounds_to_kg","implies","logical_and","logical_or","logical_not","absolute","round_number","greater_than","less_than","equals","identity","classify_intent","call_tool"],
      "call_tool_rule":"CALL TOOL is limited to the server-registered tool names and their typed input contracts. Tests use a contract stub; runtime success requires a real tool result with verified=true.",
      "rule":"Every record requires executable test vectors; Home reruns them before merge."}

@app.get("/v1/tools")
def tool_registry():
    """Public capability metadata; never exposes credentials or private provider settings."""
    return {"tools": tool_registry_data(), "execution_requires_auth": True}

@app.post("/v1/tools/execute")
def tool_execute(body: dict, request: Request):
    actor(request)
    name=body.get("tool")
    inputs=body.get("inputs", {})
    provider=body.get("provider")
    if not isinstance(name,str) or name not in TOOL_CONTRACTS:
        raise HTTPException(404,"Tool is not registered")
    if not isinstance(inputs,dict) or len(__import__("json").dumps(inputs,ensure_ascii=False).encode("utf-8")) > 45*1024*1024:
        raise HTTPException(413,"Tool input must be an object under 45MB")
    # No request URL, arbitrary model ID, code string execution, or caller credentials.
    result=execute_registered_tool(name,inputs,provider=provider)
    if result.get("status") != "verified":
        return result
    return result

@app.get("/v1/artifacts/{artifact_id}")
def tool_artifact(artifact_id: str, request: Request):
    actor(request)
    if not re.fullmatch(r"[a-f0-9]{32}\.(?:png|wav|mp4|json)",artifact_id):
        raise HTTPException(404,"Artifact not found")
    path=(TOOL_ARTIFACT_ROOT/artifact_id).resolve()
    if TOOL_ARTIFACT_ROOT not in path.parents or not path.is_file():
        raise HTTPException(404,"Artifact not found")
    media= {"png":"image/png","wav":"audio/wav","mp4":"video/mp4","json":"application/json"}[path.suffix[1:]]
    return FileResponse(path,media_type=media,filename=artifact_id)


# Shared Home cache: refresh periodically so every VORAX child sees merged methods.
_HOME_SYNC_AT=0.0
_HOME_SYNC_STATUS={"status":"not_checked","new_templates":0}

def _refresh_home_catalog():
    global _HOME_SYNC_AT,_HOME_SYNC_STATUS
    if time.monotonic()-_HOME_SYNC_AT < 60:
        return _HOME_SYNC_STATUS
    try:
        remote=sync_library(HOME_REPO,HOME_BRANCH,token=os.getenv("LEGEND_HOME_READ_TOKEN",""))
        added=0
        for record in remote:
            before=STORE.get(record["id"])
            STORE.add(record)
            if before is None: added+=1
        _HOME_SYNC_STATUS={"status":"synced","new_templates":added,"home_templates":len(remote)}
    except Exception as exc:
        _HOME_SYNC_STATUS={"status":"cached","reason":type(exc).__name__,"home_templates":len(STORE.all())}
    _HOME_SYNC_AT=time.monotonic()
    return _HOME_SYNC_STATUS


@app.post("/vorax/x")
def vorax_input_x(body: dict, request: Request):
    """Universal VORAX intake. Callers send X only; VORAX owns classification and routing."""
    value=body.get("x")
    if not isinstance(value,str) or not value.strip() or len(value)>12000:
        raise HTTPException(422,"x must be non-empty text of at most 12000 characters")
    try:
        classification=classify_input_x(value,STORE.all())
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    home_status=_refresh_home_catalog()
    # Any paid/external tool call must come through an authenticated child or admin.
    if classification.get("tool"):
        actor(request)
    result=handle_input_x(value,STORE.all(),tool_executor=execute_registered_tool)
    result["engine"]="VORAX"
    result["home_sync"]=home_status

    # Unknown X triggers bounded public-source discovery. A configured teacher may
    # propose a V-Lang method; proposal failures never replace the current response.
    if result.get("status")=="unknown" and not result.get("template_id"):
        sources=discover_method_sources(str(classification["task_type"]),value)
        result["discovery"]={"status":sources["status"],"source_count":len(sources["sources"]),
                             "sources":sources["sources"],"method_evidence":sources.get("method_evidence",[]),
                             "method_evidence_count":len(sources.get("method_evidence",[])),
                             "source_errors":sources["source_errors"],"weights_downloaded":False}
        auto_learn=os.getenv("VORAX_AUTO_LEARN","true").strip().lower() in {"1","true","yes"}
        if auto_learn and teacher_configured():
            try:
                who=actor(request)
                candidate=propose_template(
                    task=f"{classification['task_type']}: {value[:400]}",
                    inputs={"x":value},
                    triggers=[value[:48] or str(classification["task_type"])],
                    source_context=sources,
                )
                record=save_candidate(candidate,who)
                child_id=who if who!="admin" else record["provenance"].get("child_id","admin")
                submitted=submit_template(HOME_REPO,HOME_BRANCH,child_id,record,os.getenv("LEGEND_GITHUB_TOKEN",""))
                result["learning"]={"status":"submitted_for_home_verification",
                                    "template_id":record["id"],"pull_request":submitted["pull_request"]}
            except Exception as exc:
                # Learning is opportunistic. Preserve the unknown result for caller fallback.
                result["learning"]={"status":"unavailable","reason":type(exc).__name__}
        else:
            result["learning"]={"status":"teacher_not_configured" if not teacher_configured() else "disabled"}
    return result
