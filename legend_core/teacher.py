"""Optional OpenAI-compatible teacher adapter; raw completions are never persisted."""
from __future__ import annotations
import json, os, urllib.error, urllib.request
from .tlang import validate_template

def teacher_configured()->bool:
    return bool(os.getenv("LEGEND_TEACHER_API_KEY") and os.getenv("LEGEND_TEACHER_MODEL"))

def propose_template(task: str,inputs: dict,triggers: list[str])->dict:
    key=os.getenv("LEGEND_TEACHER_API_KEY"); model=os.getenv("LEGEND_TEACHER_MODEL")
    base=os.getenv("LEGEND_TEACHER_BASE_URL","https://api.openai.com/v1").rstrip("/")
    if not key or not model: raise RuntimeError("Teacher is not configured; set LEGEND_TEACHER_API_KEY and LEGEND_TEACHER_MODEL")
    if not isinstance(task,str) or not task.strip() or len(task)>500: raise ValueError("task must be 1–500 characters")
    system=("You propose T-Lang v1 procedures. Return ONLY one JSON object with keys version,id,domain,task,triggers,inputs,steps,result,tests,provenance. "
      "Use only documented T-Lang v1 operations; no source code, prose, private data, prompts, or weights. Include at least two independent executable tests. "
      "Use provenance {kind:teacher,teacher:configured-teacher}. The verifier executes every test.")
    body={"model":model,"temperature":0,"response_format":{"type":"json_object"},
      "messages":[{"role":"system","content":system},{"role":"user","content":json.dumps({"task":task,"inputs":inputs,"triggers":triggers},ensure_ascii=False)}]}
    req=urllib.request.Request(base+"/chat/completions",data=json.dumps(body).encode(),
       headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=45) as res: payload=json.loads(res.read().decode())
        content=payload["choices"][0]["message"]["content"]; candidate=json.loads(content)
    except (urllib.error.URLError,KeyError,IndexError,TypeError,json.JSONDecodeError) as exc:
        raise RuntimeError("Teacher did not return valid structured T-Lang ("+type(exc).__name__+")") from exc
    # Keep the parsed candidate only in memory; never write raw completion or request data.
    candidate["provenance"]={"kind":"teacher","teacher":"openai-compatible"}
    return validate_template(candidate)
