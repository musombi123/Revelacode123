#!/usr/bin/env python3
"""Trusted-base verifier for data-only child template pull requests."""
from __future__ import annotations
import base64,json,os,re,sys,urllib.parse,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vorax.vlang import parse_document,fingerprint
from vorax.home_index import build_method_index,parse_method_index

repo=os.environ["GITHUB_REPOSITORY"]; number=os.environ["PR_NUMBER"]; token=os.environ["GITHUB_TOKEN"]
api="https://api.github.com"
headers={"Accept":"application/vnd.github+json","Authorization":"Bearer "+token,
 "X-GitHub-Api-Version":"2022-11-28","User-Agent":"RevelaCode-Legend-verifier"}

def get(path):
    req=urllib.request.Request(api+path,headers=headers)
    with urllib.request.urlopen(req,timeout=30) as res: return json.loads(res.read().decode())

def put(path,body):
    req=urllib.request.Request(api+path,data=json.dumps(body).encode(),headers={**headers,"Content-Type":"application/json"},method="PUT")
    with urllib.request.urlopen(req,timeout=30) as res: return json.loads(res.read().decode())

pr=get(f"/repos/{repo}/pulls/{number}")
if pr["base"]["ref"]!="main" or not pr.get("head",{}).get("repo") or pr["head"]["repo"]["full_name"]!=repo:
    raise SystemExit("REJECT: only same-repository child branches targeting main are eligible")
branch=pr["head"]["ref"]
match=re.match(r"^child/([a-z0-9][a-z0-9_-]{1,39})/",branch)
branch_child=match.group(1) if match else None

def reject(message,child=branch_child):
    if child:
        try:
            runs=get(f"/repos/{repo}/actions/workflows/legend-template-gate.yml/runs?per_page=100").get("workflow_runs",[])
            own=sorted([r for r in runs if r.get("head_branch","").startswith(f"child/{child}/")
                        and r.get("status")=="completed"],key=lambda r:r.get("run_number",0),reverse=True)
            failures=0
            for run in own:
                if run.get("conclusion")=="success": break
                if run.get("conclusion")=="failure": failures+=1
            if failures>=99:
                path="/repos/"+repo+"/contents/security/blocked_children.json"
                current=get(path+"?ref=main")
                document=json.loads(base64.b64decode(current["content"]).decode())
                blocked=set(document.get("blocked_children",[])); blocked.add(child)
                document["blocked_children"]=sorted(blocked)
                put(path,{"message":f"legend: block child {child} after 100 rejected proposals",
                          "content":base64.b64encode((json.dumps(document,indent=2)+"\n").encode()).decode(),
                          "sha":current["sha"],"branch":"main"})
                print(f"HOME BLOCKED CHILD {child}: 100 consecutive rejected proposals")
        except Exception as exc:
            print("WARNING: could not persist child blocklist update: "+type(exc).__name__)
    raise SystemExit("REJECT: "+message)

if not branch_child: reject("branch must use child/<child-id>/<unique-suffix>")
blocked=get(f"/repos/{repo}/contents/security/blocked_children.json?ref={pr['base']['sha']}")
blocklist=json.loads(base64.b64decode(blocked["content"]).decode()).get("blocked_children",[])
if branch_child in blocklist: raise SystemExit("REJECT: child is blocked by Home")
runs=get(f"/repos/{repo}/actions/workflows/legend-template-gate.yml/runs?per_page=100").get("workflow_runs",[])
own=sorted([r for r in runs if r.get("head_branch","").startswith(f"child/{branch_child}/")
            and r.get("status")=="completed"],key=lambda r:r.get("run_number",0),reverse=True)
failures=0
for run in own:
    if run.get("conclusion")=="success": break
    if run.get("conclusion")=="failure": failures+=1
if failures>=100: reject("child has 100 consecutive failed Home validations")

changed=get(f"/repos/{repo}/pulls/{number}/files?per_page=100")
if not changed or len(changed)>6: reject("a child proposal must contain 1–5 V-Lang methods and one Home metadata index update")
index_changes=[item for item in changed if item.get("filename")=="templates/library/index.json"]
proposal_files=[item for item in changed if item.get("filename","").startswith("templates/library/children/")]
if len(index_changes)!=1 or index_changes[0].get("status")!="modified":
    reject("every Child method proposal must update the metadata-only Home index")
if not 1<=len(proposal_files)<=5 or len(changed)!=len(proposal_files)+1:
    reject("a child proposal may only contain 1–5 V-Lang files plus templates/library/index.json")

proposals=[]
for item in proposal_files:
    path=item["filename"]
    if not path.startswith("templates/library/children/") or not path.endswith((".vlang",".tlang")):
        reject("template PRs may only add files under templates/library/children/")
    if item["status"]!="added": reject("published templates are immutable; submit a new version ID")
    blob=get(f"/repos/{repo}/contents/{urllib.parse.quote(path,safe='/')}?ref={pr['head']['sha']}")
    try:
        records=parse_document(base64.b64decode(blob["content"]).decode("utf-8"))
    except Exception as exc:
        reject("V-Lang verifier rejected proposal: "+str(exc)[:180])
    if len(records)!=1: reject("one V-Lang record is required per child file")
    t=records[0]; child=t["provenance"].get("child_id")
    if not child or child!=branch_child: reject("V-Lang child identity does not match its branch")
    if t["provenance"].get("kind")!="teacher": reject("child submissions must identify a teacher-derived proposal")
    slug=re.sub(r"[^a-z0-9_-]+","_",t["id"].lower()).strip("_")
    expected=f"templates/library/children/{child}_{slug}.vlang"
    if path!=expected: reject("filename must be canonical for child ID and template ID")
    proposals.append(t)

tree=get(f"/repos/{repo}/git/trees/{pr['base']['sha']}?recursive=1")
if tree.get("truncated"): reject("Home tree is truncated; refusing partial duplicate check")
existing_ids=set(); existing_fps=set(); existing_tasks=set(); existing_records=[]
for node in tree.get("tree",[]):
    path=node.get("path","")
    if node.get("type")!="blob" or not path.startswith("templates/library/") or not path.endswith((".vlang",".tlang")): continue
    blob=get(f"/repos/{repo}/contents/{urllib.parse.quote(path,safe='/')}?ref={pr['base']['sha']}")
    for old in parse_document(base64.b64decode(blob["content"]).decode("utf-8")):
        existing_records.append(old)
        existing_ids.add(old["id"]); existing_fps.add(fingerprint(old))
        existing_tasks.add((old["domain"].casefold(),old["task"].casefold()))
for t in proposals:
    if t["id"] in existing_ids: reject("template ID already exists: "+t["id"])
    if fingerprint(t) in existing_fps: reject("duplicate procedure: "+t["id"])
    key=(t["domain"].casefold(),t["task"].casefold())
    if key in existing_tasks: reject("duplicate task/domain: "+t["id"])
    existing_ids.add(t["id"]); existing_fps.add(fingerprint(t)); existing_tasks.add(key)
    existing_records.append(t)

# Home verifies the index from trusted base methods plus the Child's proposed methods.
expected_index=build_method_index(existing_records)
index_blob=get(f"/repos/{repo}/contents/templates/library/index.json?ref={pr['head']['sha']}")
try:
    submitted_index=parse_method_index(base64.b64decode(index_blob["content"]).decode("utf-8"))
except Exception as exc:
    reject("Home metadata index is invalid: "+str(exc)[:180])
if submitted_index!=expected_index:
    reject("Home metadata index is not the exact metadata-only summary of Home plus proposed methods")


print(f"PASS: {len(proposals)} child template(s): structure, safety, duplicate checks, and executable tests")
