"""REVELACODE VORAX API: universal Input X over verified V-Lang methods."""
from __future__ import annotations
import hmac, os, re, time, uuid
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from vorax.engine import compose, find_template
from vorax.github_home import fetch_method_index
from vorax.store import TemplateStore
from vorax.devour import DiscoveryCatalog, discover_hf_models, conduct_prompt
from vorax.vault import VaultError
from vorax.tools import CONTRACTS as TOOL_CONTRACTS, ARTIFACT_ROOT as TOOL_ARTIFACT_ROOT, registry as tool_registry_data, execute_tool_dict as execute_registered_tool
from vorax.input_x import discover_method_sources
from vorax.builtins import handle_builtin_input

STORE=TemplateStore(os.getenv("LEGEND_DB_PATH","data/legend.sqlite3"))
DEVOUR=DiscoveryCatalog()
HOME_REPO=os.getenv("LEGEND_HOME_REPO","musombi123/Revelacode123")
HOME_BRANCH=os.getenv("LEGEND_HOME_BRANCH","main")
CHILD_ID=os.getenv("VORAX_CHILD_ID",os.getenv("LEGEND_CHILD_ID",""))
CHILD_KEY=os.getenv("VORAX_KAGGLE_CHILD_API_KEY",os.getenv("VORAX_CHILD_API_KEY",os.getenv("LEGEND_CHILD_API_KEY","")))
ADMIN_KEY=os.getenv("LEGEND_ADMIN_API_KEY","")
app=FastAPI(title="REVELACODE VORAX API",version="1.0.0",description="One Input X; trusted V-Lang verification. Home method bodies are never loaded into Children.")
origins=[x.strip() for x in os.getenv("LEGEND_CORS_ORIGINS","*").split(",") if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=["GET","POST","OPTIONS"],allow_headers=["*"])

class ComposeRequest(BaseModel):
    template_id: str|None=None
    task: str|None=None
    inputs: dict=Field(default_factory=dict)
def runtime_tool_executor(name: str, inputs: dict):
    """Use only registered providers. Kaggle never runs local model or media generation."""
    return execute_registered_tool(name, inputs)

@app.get("/health")
@app.get("/v1/health")
def health():
    return {
        "status": "ok",
        "engine": "VORAX",
        "child_role": os.getenv("VORAX_CHILD_ROLE", "shared").strip().lower(),
        "templates": len(STORE.all()),
                "home_repository": HOME_REPO,
        "home_method_bodies_loaded": False,
        "vault_configured": bool(os.getenv("HF_VAULT_TOKEN")),
        "vault_repository": os.getenv("HF_VAULT_REPO", "Musombi/revelacode-vorax-vault"),
        "local_model_loaded": False,
        "weights_downloaded": False,
    }


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

@app.get("/v1/devour/stats")
def devour_stats():
    return {"catalogued_models":len(DEVOUR.list(limit=500)),"devour":DEVOUR.stats(),"stored_templates":len(STORE.all())}

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
    role=os.getenv("VORAX_CHILD_ROLE","").strip().lower()
    media_tools={"image.generate","audio.tts","audio.transcribe","video.generate","music.generate"}
    if provider=="kaggle-gpu" and role!="kaggle":
        raise HTTPException(403,"Kaggle GPU provider is only available inside the Kaggle Child runtime")
    if role=="koyeb" and name in media_tools:
        raise HTTPException(409,"Koyeb does not execute media tools directly; submit Input X for Kaggle routing")
    if role=="kaggle" and name in media_tools:
        provider="kaggle-gpu"
    elif provider=="kaggle-gpu":
        raise HTTPException(403,"Kaggle GPU provider is only available inside the Kaggle Child runtime")
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
_HOME_INDEX = None
_HOME_INDEX_AT = 0.0


def _home_metadata_index(force: bool = False):
    global _HOME_INDEX, _HOME_INDEX_AT
    if not force and _HOME_INDEX is not None and time.monotonic() - _HOME_INDEX_AT < 60:
        return _HOME_INDEX
    token = os.getenv("LEGEND_HOME_READ_TOKEN", os.getenv("VORAX_HOME_READ_TOKEN", ""))
    if not token:
        _HOME_INDEX = None
        return None
    try:
        # Home returns only compact index metadata; no V-Lang method body is downloaded.
        _HOME_INDEX = fetch_method_index(HOME_REPO, HOME_BRANCH, token)
    except Exception:
        _HOME_INDEX = None
    _HOME_INDEX_AT = time.monotonic()
    return _HOME_INDEX


@app.get("/vorax/home-index")
def home_method_index(request: Request):
    actor(request)
    index = _home_metadata_index(force=True)
    if index is None:
        raise HTTPException(503, "Home metadata-only index is unavailable")
    return {**index, "method_bodies_included": False}


@app.post("/vorax/x")
def vorax_input_x(body: dict, request: Request):
    value = body.get("x")
    if not isinstance(value, str) or not value.strip() or len(value) > 12000:
        raise HTTPException(422, "x must be non-empty text of at most 12000 characters")
    child_id = actor(request)
    result = handle_builtin_input(value)
    index = _home_metadata_index()
    return {
        "status": result["status"],
        "engine": "VORAX",
        "method": result.get("method"),
        "answer": result.get("answer"),
        "artifact_sha256": None,
        "verification": {
            **(result.get("verification") or {}),
            "child_self_certification": False,
            "home_method_bodies_loaded": False,
        },
        "home_index_methods": index.get("methods_total") if index else None,
        "reason": result.get("reason"),
        "child_id": child_id,
    }
