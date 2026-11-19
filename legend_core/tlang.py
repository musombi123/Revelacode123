"""T-Lang v1: a data-only, typed language for verifiable procedures.
One file contains one JSON object per non-empty line. No executable code is allowed.
"""
from __future__ import annotations
import hashlib, json, math, re
from typing import Any

VERSION = "tlang/1"
ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")
CHILD_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
TYPES = {"number", "integer", "string", "boolean", "number_list"}
OPS = {"add","subtract","multiply","divide","percentage_of","percentage_change","average","minimum","maximum","sort_ascending","sort_descending","unique","count","word_count","uppercase","lowercase","reverse_text","celsius_to_fahrenheit","fahrenheit_to_celsius","km_to_miles","miles_to_km","kg_to_pounds","pounds_to_kg","implies","logical_and","logical_or","logical_not","absolute","round_number","greater_than","less_than","equals","identity"}
SECRET_PATTERNS = [
 re.compile(r"(?i)\b(?:sk-[a-z0-9_-]{12,}|gh[pousr]_[a-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"),
 re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
 re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{16,}"),
 re.compile(r"(?i)\b(password|passwd|client_secret|api_key)\s*[:=]\s*\S+"),
 re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
 re.compile(r"(?<!\w)(?:\+?254|0)7\d{8}(?!\w)"),
]
TOP_KEYS={"version","id","domain","task","triggers","inputs","steps","result","tests","provenance"}

class TemplateError(ValueError):
    """Malformed, unsafe, or unproven T-Lang record."""

def _reject_private(value: Any, path: str="$") -> None:
    if isinstance(value,str):
        for pattern in SECRET_PATTERNS:
            if pattern.search(value): raise TemplateError(f"Potential private data or credential at {path}")
    elif isinstance(value,dict):
        for key,child in value.items():
            _reject_private(key,f"{path}.<key>"); _reject_private(child,f"{path}.{key}")
    elif isinstance(value,list):
        for i,child in enumerate(value): _reject_private(child,f"{path}[{i}]")

def _resolve(value: Any, inputs: dict, steps: dict) -> Any:
    if isinstance(value,dict):
        if set(value)=={"input"} and isinstance(value["input"],str):
            if value["input"] not in inputs: raise TemplateError(f"Unknown input reference: {value['input']}")
            return inputs[value["input"]]
        if set(value)=={"step"} and isinstance(value["step"],str):
            if value["step"] not in steps: raise TemplateError(f"Unknown or forward step reference: {value['step']}")
            return steps[value["step"]]
        raise TemplateError("Arguments may only be literals or input/step references")
    if isinstance(value,(str,int,float,bool)) or value is None: return value
    raise TemplateError("Unsupported argument value")

def _number(value: Any) -> float:
    if isinstance(value,bool) or not isinstance(value,(int,float)): raise TemplateError("Operation requires numeric arguments")
    n=float(value)
    if not math.isfinite(n): raise TemplateError("Non-finite numbers are not permitted")
    return n

def _apply(op: str,args: list[Any]) -> Any:
    if op not in OPS: raise TemplateError(f"Unsupported operation: {op}")
    if op in {"add","subtract","multiply","divide","percentage_of","percentage_change","greater_than","less_than","equals"}:
        if len(args)!=2: raise TemplateError(f"{op} requires exactly two arguments")
        a,b=args
        if op=="equals": return a==b
        if op in {"greater_than","less_than"}:
            a,b=_number(a),_number(b)
            return a>b if op=="greater_than" else a<b
        a,b=_number(a),_number(b)
        if op=="add": return a+b
        if op=="subtract": return a-b
        if op=="multiply": return a*b
        if op=="divide":
            if b==0: raise TemplateError("Division by zero")
            return a/b
        if op=="percentage_of": return (a/100.0)*b
        if a==0: raise TemplateError("Percentage change from zero is undefined")
        return ((b-a)/abs(a))*100.0
    if op in {"average","minimum","maximum","sort_ascending","sort_descending","unique"}:
        if len(args)!=1 or not isinstance(args[0],list): raise TemplateError(f"{op} requires one list")
        values=args[0]
        if not values: raise TemplateError(f"{op} does not accept an empty list")
        if op=="unique":
            out=[]
            for item in values:
                if item not in out: out.append(item)
            return out
        nums=[_number(x) for x in values]
        if op=="average": return sum(nums)/len(nums)
        if op=="minimum": return min(nums)
        if op=="maximum": return max(nums)
        return sorted(nums,reverse=op=="sort_descending")
    if op=="count":
        if len(args)!=1 or not isinstance(args[0],(list,str)): raise TemplateError("count requires one list or string")
        return len(args[0])
    if op=="word_count":
        if len(args)!=1 or not isinstance(args[0],str): raise TemplateError("word_count requires one string")
        return len(args[0].split())
    if op in {"uppercase","lowercase","reverse_text"}:
        if len(args)!=1 or not isinstance(args[0],str): raise TemplateError(f"{op} requires one string")
        return args[0].upper() if op=="uppercase" else args[0].lower() if op=="lowercase" else args[0][::-1]
    if op in {"celsius_to_fahrenheit","fahrenheit_to_celsius","km_to_miles","miles_to_km","kg_to_pounds","pounds_to_kg","absolute"}:
        if len(args)!=1: raise TemplateError(f"{op} requires one argument")
        x=_number(args[0])
        funcs={"celsius_to_fahrenheit":lambda n:n*9/5+32,"fahrenheit_to_celsius":lambda n:(n-32)*5/9,"km_to_miles":lambda n:n*0.621371192237334,"miles_to_km":lambda n:n/0.621371192237334,"kg_to_pounds":lambda n:n*2.204622621848776,"pounds_to_kg":lambda n:n/2.204622621848776,"absolute":abs}
        return funcs[op](x)
    if op=="round_number":
        if len(args) not in {1,2}: raise TemplateError("round_number takes value and optional digits")
        digits=int(args[1]) if len(args)==2 else 0
        if digits<0 or digits>10: raise TemplateError("round digits must be between 0 and 10")
        return round(_number(args[0]),digits)
    if op=="implies":
        if len(args)!=2 or not all(isinstance(x,bool) for x in args): raise TemplateError("implies requires two booleans")
        return (not args[0]) or args[1]
    if op in {"logical_and","logical_or"}:
        if len(args)<2 or not all(isinstance(x,bool) for x in args): raise TemplateError(f"{op} requires at least two booleans")
        return all(args) if op=="logical_and" else any(args)
    if op=="logical_not":
        if len(args)!=1 or not isinstance(args[0],bool): raise TemplateError("logical_not requires one boolean")
        return not args[0]
    if op=="identity":
        if len(args)!=1: raise TemplateError("identity requires one argument")
        return args[0]
    raise TemplateError(f"Unsupported operation: {op}")

def execute(template: dict,payload: dict) -> tuple[Any,list[dict]]:
    if not isinstance(payload,dict) or set(payload)!=set(template["inputs"]): raise TemplateError("Input keys must exactly match the declared input schema")
    checks={
      "number":lambda x:isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(float(x)),
      "integer":lambda x:isinstance(x,int) and not isinstance(x,bool),
      "string":lambda x:isinstance(x,str),"boolean":lambda x:isinstance(x,bool),
      "number_list":lambda x:isinstance(x,list) and all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(float(v)) for v in x)
    }
    for name,kind in template["inputs"].items():
        if not checks[kind](payload[name]): raise TemplateError(f"Input '{name}' must be {kind}")
    values={}; trace=[]
    for step in template["steps"]:
        args=[_resolve(arg,payload,values) for arg in step["args"]]
        output=_apply(step["op"],args); values[step["as"]]=output
        trace.append({"step":step["as"],"operation":step["op"],"arguments":args,"result":output})
    return values[template["result"]["step"]],trace

def _equal(a: Any,b: Any) -> bool:
    if isinstance(a,(int,float)) and not isinstance(a,bool) and isinstance(b,(int,float)) and not isinstance(b,bool):
        return math.isclose(float(a),float(b),rel_tol=1e-9,abs_tol=1e-9)
    return a==b

def validate_template(t: Any,*,run_tests: bool=True) -> dict:
    if not isinstance(t,dict): raise TemplateError("A T-Lang record must be a JSON object")
    if set(t)!=TOP_KEYS:
        raise TemplateError(f"Invalid top-level fields; missing={sorted(TOP_KEYS-set(t))}, extra={sorted(set(t)-TOP_KEYS)}")
    _reject_private(t)
    if t["version"]!=VERSION: raise TemplateError(f"version must be {VERSION}")
    if not isinstance(t["id"],str) or not ID_RE.fullmatch(t["id"]): raise TemplateError("id must be a lowercase dotted identifier")
    for field in ("domain","task"):
        if not isinstance(t[field],str) or not t[field].strip() or len(t[field])>160: raise TemplateError(f"{field} must be a non-empty string of at most 160 characters")
    triggers=t["triggers"]
    if not isinstance(triggers,list) or not 1<=len(triggers)<=12 or any(not isinstance(x,str) or not x.strip() or len(x)>48 for x in triggers):
        raise TemplateError("triggers must contain 1 to 12 short phrases")
    if len({x.casefold() for x in triggers})!=len(triggers): raise TemplateError("Duplicate triggers are not allowed")
    inputs=t["inputs"]
    if not isinstance(inputs,dict) or not 1<=len(inputs)<=12: raise TemplateError("inputs must declare 1 to 12 values")
    for name,kind in inputs.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}",name) or kind not in TYPES: raise TemplateError(f"Invalid input declaration: {name}")
    steps=t["steps"]
    if not isinstance(steps,list) or not 1<=len(steps)<=20: raise TemplateError("steps must contain 1 to 20 operations")
    names=set()
    for step in steps:
        if not isinstance(step,dict) or set(step)!={"op","args","as"}: raise TemplateError("Each step must contain exactly op, args, and as")
        if step["op"] not in OPS: raise TemplateError(f"Operation is not allowed: {step['op']}")
        if not isinstance(step["as"],str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}",step["as"]) or step["as"] in names: raise TemplateError("Step outputs must be unique identifiers")
        if not isinstance(step["args"],list) or len(step["args"])>12: raise TemplateError("Step args must be a list with at most 12 values")
        for arg in step["args"]:
            if isinstance(arg,dict):
                if set(arg)=={"input"} and arg["input"] in inputs: continue
                if set(arg)=={"step"} and arg["step"] in names: continue
                raise TemplateError("Invalid or forward reference in step arguments")
            if not (arg is None or isinstance(arg,(str,int,float,bool))): raise TemplateError("Nested objects and arrays are not valid literals")
            if isinstance(arg,float) and not math.isfinite(arg): raise TemplateError("Non-finite literal")
        names.add(step["as"])
    if not isinstance(t["result"],dict) or set(t["result"])!={"step"} or t["result"]["step"] not in names: raise TemplateError("result must reference a declared step")
    tests=t["tests"]
    if not isinstance(tests,list) or not 2<=len(tests)<=20: raise TemplateError("At least two and at most twenty executable tests are required")
    for test in tests:
        if not isinstance(test,dict) or set(test)!={"input","expect"} or not isinstance(test["input"],dict) or set(test["input"])!=set(inputs):
            raise TemplateError("Each test must contain exactly input and expect, with all declared inputs")
    p=t["provenance"]
    if not isinstance(p,dict) or "kind" not in p or set(p)-{"kind","child_id","teacher"}: raise TemplateError("Invalid provenance fields")
    if p["kind"] not in {"seed","teacher","manual"}: raise TemplateError("provenance.kind must be seed, teacher, or manual")
    if "child_id" in p and (not isinstance(p["child_id"],str) or not CHILD_RE.fullmatch(p["child_id"])): raise TemplateError("Invalid provenance.child_id")
    if "teacher" in p and (not isinstance(p["teacher"],str) or len(p["teacher"])>64): raise TemplateError("Invalid provenance.teacher")
    if run_tests:
        for i,test in enumerate(tests):
            try: actual,_=execute(t,test["input"])
            except Exception as exc: raise TemplateError(f"Test {i+1} could not execute: {exc}") from exc
            if not _equal(actual,test["expect"]): raise TemplateError(f"Test {i+1} failed: expected {test['expect']!r}, got {actual!r}")
    return t

def parse_document(text: str,*,run_tests: bool=True) -> list[dict]:
    if not isinstance(text,str) or len(text.encode("utf-8"))>256_000: raise TemplateError("T-Lang document exceeds 256 KB")
    records=[]
    for line_no,line in enumerate(text.splitlines(),1):
        if not line.strip(): continue
        try: records.append(validate_template(json.loads(line),run_tests=run_tests))
        except (json.JSONDecodeError,TemplateError) as exc: raise TemplateError(f"Line {line_no}: {exc}") from exc
    if not records: raise TemplateError("No T-Lang records found")
    ids=[x["id"] for x in records]
    if len(ids)!=len(set(ids)): raise TemplateError("Duplicate template IDs inside document")
    return records

def fingerprint(t: dict) -> str:
    material={k:v for k,v in t.items() if k!="provenance"}
    raw=json.dumps(material,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def dump_record(t: dict) -> str:
    validate_template(t)
    return json.dumps(t,sort_keys=True,separators=(",",":"),ensure_ascii=False)+"\n"
