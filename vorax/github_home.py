"""GitHub Home sync and child-to-Home pull request submission."""
from __future__ import annotations
import base64,json,os,re,time,urllib.error,urllib.parse,urllib.request,uuid
from .vlang import dump_record
from .home_index import append_method, parse_method_index

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

def fetch_method_index(repo: str, branch: str = "main", token: str = "") -> dict:
    """Read only the metadata index from Home. Never fetch V-Lang method bodies."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid Home repository")
    path = f"/repos/{repo}/contents/templates/library/index.json?ref={urllib.parse.quote(branch, safe='')}"
    try:
        blob = _api("GET", path, token)
    except GitHubHomeError as exc:
        raise GitHubHomeError("Home metadata index is unavailable; refusing to learn from Home method contents") from exc
    if blob.get("encoding") != "base64":
        raise GitHubHomeError("Home method index is not base64 content")
    try:
        raw = base64.b64decode(blob["content"], validate=False)
        return parse_method_index(raw)
    except Exception as exc:
        raise GitHubHomeError("Home metadata index failed validation") from exc

def submit_template(repo: str, branch: str, child_id: str, template: dict, token: str) -> dict:
    """Submit a teacher-derived V-Lang proposal and metadata-only index update as one Home PR."""
    if not token:
        raise GitHubHomeError("VORAX_HOME_WRITE_TOKEN is not configured on this Runtime")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,39}", child_id):
        raise ValueError("Invalid child ID")
    record = dict(template)
    provenance = dict(record.get("provenance", {}))
    provenance.update({"kind": "teacher", "child_id": child_id})
    record["provenance"] = provenance
    content = dump_record(record)

    # Only a compact metadata index leaves Home; method bodies are never fetched into a Child.
    home_index = fetch_method_index(repo, branch, token)
    try:
        updated_index = append_method(home_index, record)
    except ValueError as exc:
        raise GitHubHomeError("Home metadata index reports a duplicate method: " + str(exc)) from exc

    slug = re.sub(r"[^a-z0-9_-]+", "_", record["id"].lower()).strip("_")
    path = f"templates/library/children/{child_id}_{slug}.vlang"
    new_branch = f"child/{child_id}/{int(time.time())}-{uuid.uuid4().hex[:8]}"
    ref = _api("GET", f"/repos/{repo}/git/ref/heads/{urllib.parse.quote(branch, safe='')}", token)
    _api("POST", f"/repos/{repo}/git/refs", token, {
        "ref": "refs/heads/" + new_branch,
        "sha": ref["object"]["sha"],
    })

    encoded_method = base64.b64encode(content.encode("utf-8")).decode("ascii")
    _api("PUT", f"/repos/{repo}/contents/{urllib.parse.quote(path, safe='/')}", token, {
        "message": f"vorax: child {child_id} proposes {record['id']}",
        "content": encoded_method,
        "branch": new_branch,
    })

    index_path = "templates/library/index.json"
    encoded_index = base64.b64encode(
        (json.dumps(updated_index, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    ).decode("ascii")
    _api("PUT", f"/repos/{repo}/contents/{index_path}", token, {
        "message": f"vorax: update Home metadata index for {record['id']}",
        "content": encoded_index,
        "branch": new_branch,
        "sha": ref.get("index_blob_sha"),
    }) if False else _api(
        "PUT",
        f"/repos/{repo}/contents/{index_path}",
        token,
        {
            "message": f"vorax: update Home metadata index for {record['id']}",
            "content": encoded_index,
            "branch": new_branch,
        },
    )

    pr = _api("POST", f"/repos/{repo}/pulls", token, {
        "title": f"VORAX V-Lang proposal: {record['id']}",
        "body": (
            f"Knowledge proposal from Child {child_id}. The PR contains a testable V-Lang method "
            "and its metadata-only Home index update. Home validates schema, safety, duplicates, "
            "index integrity, and executable tests. No Child self-certification is accepted."
        ),
        "head": new_branch,
        "base": branch,
    })
    return {
        "pull_request": pr["html_url"],
        "number": pr["number"],
        "branch": new_branch,
        "template_id": record["id"],
        "file": path,
        "index_file": index_path,
        "home_authority": "pending_review",
    }
