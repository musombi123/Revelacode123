"""Deterministic template selection and composition; unknown is first-class."""
from __future__ import annotations
import re
from .vlang import execute, TemplateError

def _normalise(value: str)->str:
    return re.sub(r"[^a-z0-9 ]+"," ",value.casefold()).strip()

def find_template(task: str,catalog: list[dict]):
    query=_normalise(task)
    if not query: return None,"empty_task"
    matches=[]
    for template in catalog:
        phrases=[template["task"],*template["triggers"]]
        score=max((len(_normalise(p)) for p in phrases if _normalise(p) and _normalise(p) in query),default=0)
        if score: matches.append((score,template))
    if not matches: return None,"unknown"
    matches.sort(key=lambda x:x[0],reverse=True)
    if len(matches)>1 and matches[0][0]==matches[1][0]: return None,"ambiguous"
    return matches[0][1],None

def compose(catalog: list[dict],*,template_id: str|None=None,task: str|None=None,inputs: dict|None=None,tool_executor=None)->dict:
    template=next((x for x in catalog if x["id"]==template_id),None) if template_id else None
    reason=None
    if template_id and template is None: reason="unknown"
    if template is None and task: template,reason=find_template(task,catalog)
    if template is None:
        return {"status":reason or "unknown","answer":None,"proof":[],
                "message":"No verified procedure matches this task. Do not infer an answer; add a teacher-backed, testable V-Lang method."}
    try: result,trace=execute(template,inputs or {},tool_executor=tool_executor)
    except TemplateError as exc:
        return {"status":"input_required","answer":None,"template_id":template["id"],"message":str(exc),"proof":[]}
    return {"status":"verified","answer":result,"template_id":template["id"],
            "message":"Computed using a locally verified procedure.",
            "proof":[{"template":template["id"],"task":template["task"]},*trace]}
