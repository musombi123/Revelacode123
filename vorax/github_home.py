"""GitHub Home sync and child-to-Home pull request submission."""
from __future__ import annotations
import base64,json,os,re,time,urllib.error,urllib.parse,urllib.request,uuid
from .vlang import dump_record,parse_document

class GitHubHomeError(RuntimeError): pass

def _api(method,path,token=None,body=None):
    headers={"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","User-Agent":"RevelaCode-Legend/0.1"}
    if token: headers["Authorization"]="Bearer "+token
    data=json.dumps(body).encode() if body is not None else None
    if data is not None: headers["Content-Type"]="application/json"
    req=urllib.request.Request("https://api.github.com"+path,data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=30) as res:
            raw=res.read(); return json.loads(raw.decode()) if raw else {}
    except urllib.error.HTTPError as exc:
        detail=exc.read(2048).decode("utf-8","replace")
        raise GitHubHomeError(f"GitHub API {method} failed ({exc.code}): {detail[:400]}") from exc
    except urllib.error.URLError as exc: raise GitHubHomeError("GitHub API unavailable: "+type(exc).__name__) from exc

def sync_library(repo: str,branch: str="main",token: str="")->list[dict]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",repo): raise ValueError("Invalid Home repository")
    tree=_api("GET",f"/repos/{repo}/git/trees/{urllib.parse.quote(branch,safe='')}?recursive=1",token)
    if tree.get("truncated"): raise GitHubHomeError("Home tree is truncated; refusing a partial sync")
    paths=[x["path"] for x in tree.get("tree",[]) if x.get("type")=="blob" and x.get("path","").startswith("templates/library/") and x["path"].endswith((".vlang",".tlang"))]
    result=[]
    for path in paths:
        try:
            content=_api("GET",f"/repos/{repo}/contents/{urllib.parse.quote(path,safe='/')}?ref={urllib.parse.quote(branch,safe='')}",token)
            if content.get("encoding")!="base64": raise GitHubHomeError(f"Home content encoding is not base64: {path}")
            result.extend(parse_document(base64.b64decode(content["content"]).decode("utf-8")))
        except Exception as exc: raise GitHubHomeError(f"Home sync rejected {path}: {type(exc).__name__}") from exc
    return result

def submit_template(repo: str,branch: str,child_id: str,template: dict,token: str)->dict:
    if not token: raise GitHubHomeError("LEGEND_GITHUB_TOKEN is not configured on this child")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,39}",child_id): raise ValueError("Invalid child ID")
    record=dict(template); provenance=dict(record.get("provenance",{}))
    provenance.update({"kind":"teacher" if provenance.get("kind")=="teacher" else "manual","child_id":child_id})
    record["provenance"]=provenance; content=dump_record(record)
    slug=re.sub(r"[^a-z0-9_-]+","_",record["id"].lower()).strip("_")
    path=f"templates/library/children/{child_id}_{slug}.vlang"
    new_branch=f"child/{child_id}/{int(time.time())}-{uuid.uuid4().hex[:8]}"
    ref=_api("GET",f"/repos/{repo}/git/ref/heads/{urllib.parse.quote(branch,safe='')}",token)
    _api("POST",f"/repos/{repo}/git/refs",token,{"ref":"refs/heads/"+new_branch,"sha":ref["object"]["sha"]})
    encoded=base64.b64encode(content.encode()).decode("ascii")
    _api("PUT",f"/repos/{repo}/contents/{urllib.parse.quote(path,safe='/')}",token,
         {"message":f"vorax: child {child_id} proposes {record['id']}","content":encoded,"branch":new_branch})
    pr=_api("POST",f"/repos/{repo}/pulls",token,
       {"title":f"VORAX V-Lang proposal: {record['id']}",
        "body":f"Automated V-Lang proposal from child {child_id}. Home will verify schema, safety, duplicates, and executable tests.",
        "head":new_branch,"base":branch})
    return {"pull_request":pr["html_url"],"number":pr["number"],"branch":new_branch,"template_id":record["id"],"file":path}


def enqueue_kaggle_x(repo: str, value: str, token: str) -> dict:
    """Queue heavy X for the ephemeral Kaggle worker; this function does not execute it."""
    if not token:
        raise GitHubHomeError("LEGEND_GITHUB_TOKEN is not configured for queue creation")
    if not isinstance(value, str) or not value.strip() or len(value) > 12000:
        raise ValueError("X must be non-empty text of at most 12000 characters")
    if re.search(r"(?i)(api[_ -]?key|password|secret|token)\s*[:=]\s*\S+", value):
        raise ValueError("X looks like it may contain a credential; remove it before queuing")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid Home repository")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = {"version": "vorax-x-queue/v1", "status": "queued", "x": value,
               "queued_at": stamp, "source": "koyeb_input_x"}
    body = ("<!-- VORAX_X_QUEUE_V1 -->\n" + json.dumps(payload, ensure_ascii=False, indent=2) +
            "\n\nDo not put passwords, API keys, private records, or sensitive personal data in this queue.")
    issue = _api("POST", f"/repos/{repo}/issues", token,
                 {"title": "[VORAX-X] " + time.strftime("%Y%m%d-%H%M%S", time.gmtime()), "body": body})
    return {"number": issue.get("number"), "url": issue.get("html_url"),
            "status": "queued", "destination_child": "kaggle-child", "verification": "not_executed"}
