"""Legend's bounded tool execution boundary.

Providers are replaceable. Tool contracts are stable and never accept caller-supplied
URLs, Python snippets, or arbitrary provider/model identifiers. Only verified artifacts
are returned as successful results.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import time
import zlib
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ARTIFACT_ROOT = Path(os.getenv("LEGEND_ARTIFACT_DIR", "data/artifacts")).resolve()
MAX_INPUT_CHARS = 12_000
MAX_ARTIFACT_BYTES = 40 * 1024 * 1024
HF_BASE = "https://router.huggingface.co/hf-inference/models/"
DEFAULT_MODELS = {
    "image.generate": "stabilityai/stable-diffusion-2-1",
    "audio.tts": "facebook/mms-tts-eng",
    "audio.transcribe": "facebook/wav2vec2-base-960h",
    "video.generate": "Lightricks/LTX-Video",
    "music.generate": "facebook/musicgen-small",
    "translate": "Helsinki-NLP/opus-mt-en-sw",
}
MODEL_ENV = {
    name: "LEGEND_MODEL_" + name.upper().replace(".", "_")
    for name in DEFAULT_MODELS
}

class ToolError(RuntimeError):
    """A tool failed, was unavailable, or produced an unverifiable result."""

@dataclass(frozen=True)
class ToolContract:
    name: str
    inputs: dict[str, str]
    outputs: dict[str, str]
    timeout_seconds: int
    cost: str
    provider: str
    artifact_kind: str

@dataclass
class ToolResult:
    status: str
    tool: str
    provider: str
    artifact: str | None = None
    mime_type: str | None = None
    size_bytes: int = 0
    duration_seconds: float | None = None
    output: Any = None
    verification: dict[str, Any] | None = None
    elapsed_ms: int = 0
    reason: str | None = None
    sha256: str | None = None
    model_id: str | None = None
    gpu_telemetry: dict[str, Any] | None = None

CONTRACTS = {
    "image.generate": ToolContract("image.generate", {"prompt":"string","width":"integer","height":"integer"}, {"artifact":"image/png","width":"integer","height":"integer"}, 60, "1 provider inference call", "huggingface", "png"),
    "audio.tts": ToolContract("audio.tts", {"text":"string"}, {"artifact":"audio/wav","duration_seconds":"number"}, 60, "1 provider inference call", "huggingface", "wav"),
    "audio.transcribe": ToolContract("audio.transcribe", {"audio_base64":"string"}, {"text":"string"}, 60, "1 provider inference call", "huggingface", "json"),
    "video.generate": ToolContract("video.generate", {"prompt":"string"}, {"artifact":"video/mp4","duration_seconds":"number"}, 120, "1 provider inference call", "huggingface", "mp4"),
    "music.generate": ToolContract("music.generate", {"prompt":"string"}, {"artifact":"audio/wav","duration_seconds":"number"}, 120, "1 provider inference call", "huggingface", "wav"),
    "translate": ToolContract("translate", {"text":"string","target_language":"string"}, {"text":"string"}, 60, "1 provider inference call", "huggingface", "json"),
    "code.execute": ToolContract("code.execute", {"source":"string","tests":"array"}, {"result":"json"}, 10, "local bounded interpreter", "legend-local", "json"),
}

def registry() -> list[dict[str, Any]]:
    return [{
        "name": c.name, "inputs": c.inputs, "outputs": c.outputs,
        "timeout_seconds": c.timeout_seconds, "cost": c.cost,
        "provider": c.provider, "replaceable": True,
        "configured": _provider_configured(c.name),
    } for c in CONTRACTS.values()]

def _provider_configured(name: str) -> bool:
    if name == "code.execute":
        return True
    token = os.getenv("HF_INFERENCE_TOKEN") or os.getenv("HF_TOKEN")
    return bool(token and (os.getenv(MODEL_ENV.get(name, ""), "") or DEFAULT_MODELS.get(name)))

def _string(value: Any, field: str, limit: int = MAX_INPUT_CHARS) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ToolError(f"{field} must be a non-empty string (max {limit} characters)")
    return value

def _dimensions(width: Any, height: Any) -> tuple[int, int]:
    if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int):
        raise ToolError("width and height must be integers")
    if not (64 <= width <= 1024 and 64 <= height <= 1024 and width * height <= 1_048_576):
        raise ToolError("Image dimensions exceed the 1024px / 1MP safety limit")
    return width, height

def _model_for(name: str) -> str:
    model = os.getenv(MODEL_ENV.get(name, ""), DEFAULT_MODELS.get(name, ""))
    if not model or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", model):
        raise ToolError("No approved provider model is configured for this tool")
    return model

def _hf(name: str, payload: Any, timeout: int, *, binary: bool) -> tuple[bytes, str]:
    allow_billable = os.getenv("VORAX_ALLOW_BILLABLE_INFERENCE", "").strip().casefold() in {"1", "true", "yes"}
    if not allow_billable:
        raise ToolError(
            "Remote tool-provider calls are disabled by default; enable "
            "VORAX_ALLOW_BILLABLE_INFERENCE explicitly before using a billable adapter."
        )
    token = os.getenv("HF_INFERENCE_TOKEN") or os.getenv("HF_TOKEN")
    if not token:
        raise ToolError("Hugging Face inference token is not configured")
    model = _model_for(name)
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": "Bearer " + token}
    headers["Content-Type"] = "application/octet-stream" if isinstance(payload, bytes) else "application/json"
    req = Request(HF_BASE + model, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as response:
            data = response.read(MAX_ARTIFACT_BYTES + 1)
            mime = response.headers.get("Content-Type", "application/octet-stream").split(";")[0].lower()
    except HTTPError as exc:
        detail = exc.read(1024).decode("utf-8", "replace")
        raise ToolError(f"Provider returned HTTP {exc.code}: {detail[:300]}") from exc
    except (URLError, TimeoutError) as exc:
        raise ToolError(f"Provider request failed: {type(exc).__name__}") from exc
    if not data or len(data) > MAX_ARTIFACT_BYTES:
        raise ToolError("Provider response is empty or exceeds the 40MB artifact limit")
    if not binary:
        if "json" not in mime and not mime.startswith("text/"):
            raise ToolError(f"Expected structured text response, received {mime}")
    return data, mime

def _save(data: bytes, suffix: str) -> Path:
    if not data or len(data) > MAX_ARTIFACT_BYTES:
        raise ToolError("Artifact is empty or too large")
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    path = (ARTIFACT_ROOT / (os.urandom(16).hex() + suffix)).resolve()
    if ARTIFACT_ROOT not in path.parents:
        raise ToolError("Artifact path escaped the configured directory")
    path.write_bytes(data)
    return path

def _verify_png(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) < 33 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ToolError("Provider output is not a valid PNG")
    length = struct.unpack(">I", raw[8:12])[0]
    if raw[12:16] != b"IHDR" or length != 13:
        raise ToolError("PNG header is invalid")
    width, height = struct.unpack(">II", raw[16:24])
    if width < 1 or height < 1 or width * height > 1_048_576:
        raise ToolError("PNG dimensions are invalid or exceed safety limits")
    chunks=[]; offset=8; seen_end=False
    while offset + 12 <= len(raw):
        size=struct.unpack(">I",raw[offset:offset+4])[0]
        kind=raw[offset+4:offset+8]
        end=offset+12+size
        if end>len(raw): raise ToolError("PNG chunk exceeds file boundary")
        payload=raw[offset+8:offset+8+size]
        crc=struct.unpack(">I",raw[offset+8+size:end])[0]
        if zlib.crc32(kind+payload)&0xffffffff != crc: raise ToolError("PNG chunk checksum failed")
        if kind==b"IDAT": chunks.append(payload)
        if kind==b"IEND": seen_end=True; break
        offset=end
    if not chunks or not seen_end: raise ToolError("PNG image data or IEND chunk is missing")
    try: decoded=zlib.decompress(b"".join(chunks))
    except zlib.error as exc: raise ToolError("PNG pixel stream cannot be decoded") from exc
    bit_depth=raw[24]; color_type=raw[25]
    channels={0:1,2:3,3:1,4:2,6:4}.get(color_type)
    if bit_depth not in {8,16} or channels is None: raise ToolError("PNG color format is unsupported by the verifier")
    expected=height*(1+width*channels*(bit_depth//8))
    if len(decoded)!=expected: raise ToolError("PNG decoded pixel buffer has an invalid length")
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
            extrema=ImageStat.Stat(image.convert("RGB")).extrema
            if all(low==high for low,high in extrema):
                raise ToolError("PNG contains a single-colour blank image")
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError("PNG could not be independently decoded by Pillow") from exc
    return {"valid": True, "format":"png", "width":width, "height":height, "decoded_pixel_bytes":len(decoded), "non_blank":True}

def _verify_wav(path: Path) -> tuple[dict[str, Any], float]:
    try:
        with wave.open(str(path), "rb") as wav:
            frames, rate, channels, width = wav.getnframes(), wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
            duration = frames / float(rate) if rate else 0
            if wav.getcomptype() != "NONE" or frames <= 0 or rate <= 0 or channels <= 0 or width <= 0 or duration <= 0:
                raise ToolError("WAV has no supported playable PCM audio frames")
            if duration > 600:
                raise ToolError("WAV duration exceeds 10-minute limit")
            pcm=wav.readframes(frames)
            silent = (all(sample == 128 for sample in pcm) if width == 1 else not any(pcm))
            if silent: raise ToolError("WAV contains silence only")
            return {"valid":True,"format":"wav","frames":frames,"sample_rate":rate,"channels":channels,"non_silent":True}, duration
    except (wave.Error, EOFError) as exc:
        raise ToolError("Provider output is not a readable WAV") from exc

def _verify_mp4(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) < 16 or raw[4:8] != b"ftyp":
        raise ToolError("Provider output is not an MP4 container")
    if b"moov" not in raw or b"mdat" not in raw:
        raise ToolError("MP4 is missing movie metadata or media data")
    try:
        probe=subprocess.run(
            ["ffprobe","-v","error","-select_streams","v:0","-count_frames",
             "-show_entries","stream=nb_read_frames,duration","-of","json",str(path)],
            capture_output=True,text=True,timeout=8,check=True,
        )
        streams=json.loads(probe.stdout).get("streams",[])
        if not streams: raise ToolError("MP4 contains no video stream")
        frames=int(streams[0].get("nb_read_frames") or 0)
        duration=float(streams[0].get("duration") or 0)
        if frames<=10 or duration<=0: raise ToolError("MP4 must contain more than 10 decoded frames and positive duration")
        return {"valid":True,"format":"mp4","frames":frames,"duration_seconds":duration}
    except FileNotFoundError as exc:
        raise ToolError("ffprobe is required to verify MP4 frames and duration") from exc
    except (subprocess.SubprocessError,ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
        raise ToolError("MP4 temporal verification failed") from exc

def _run_code(source: str, tests: Any) -> ToolResult:
    # This is deliberately not arbitrary Python execution. It validates a restricted
    # expression and evaluates only a JSON literal expression with no names/calls.
    import ast
    source = _string(source, "source", 4000)
    if not isinstance(tests, list) or len(tests) > 20:
        raise ToolError("tests must be an array with at most 20 cases")
    if tests:
        raise ToolError("code.execute currently accepts no inline test vectors; executable test-runner support is not yet implemented")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ToolError("Code expression failed syntax validation") from exc
    allowed = (ast.Expression, ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.UnaryOp, ast.USub, ast.UAdd, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.Load)
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise ToolError("Only literal arithmetic expressions are currently executable; calls, names and statements are forbidden")
    if len(list(ast.walk(tree))) > 100:
        raise ToolError("Expression exceeds the 100-node execution limit")
    # No eval/exec: compute supported arithmetic using a tiny recursive interpreter.
    def calc(node):
        if isinstance(node, ast.Expression): return calc(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int,float)) and not isinstance(node.value,bool): return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub,ast.UAdd)):
            value=calc(node.operand); return -value if isinstance(node.op,ast.USub) else value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Mod,ast.Pow)):
            a,b=calc(node.left),calc(node.right)
            if abs(a)>1e12 or abs(b)>1e12: raise ToolError("Numeric bounds exceeded")
            if isinstance(node.op,ast.Add): value=a+b
            elif isinstance(node.op,ast.Sub): value=a-b
            elif isinstance(node.op,ast.Mult): value=a*b
            elif isinstance(node.op,ast.Div):
                if b==0: raise ToolError("Division by zero")
                value=a/b
            elif isinstance(node.op,ast.Mod):
                if b==0: raise ToolError("Modulo by zero")
                value=a%b
            else:
                if abs(b)>10 or abs(a)>1e6: raise ToolError("Exponent bounds exceeded")
                value=a**b
            if not isinstance(value,(int,float)) or abs(value)>1e12: raise ToolError("Result bounds exceeded")
            return value
        raise ToolError("Unsupported expression node")
    result=calc(tree)
    artifact=_save(json.dumps({"expression":source,"result":result}).encode(), ".json")
    return ToolResult("verified","code.execute","legend-local",artifact.name,"application/json",artifact.stat().st_size,output={"result":result},verification={"valid":True,"interpreter":"restricted-arithmetic-v1"})

def execute_tool(name: str, inputs: dict[str, Any], *, provider: str | None = None) -> ToolResult:
    if name not in CONTRACTS:
        raise ToolError("Tool is not registered")
    if not isinstance(inputs, dict):
        raise ToolError("Tool inputs must be an object")
    contract=CONTRACTS[name]
    started=time.monotonic()
    if name == "code.execute":
        if provider not in (None, "huggingface"):
            raise ToolError("The bounded code interpreter does not use a media provider")
        result=_run_code(inputs.get("source"), inputs.get("tests", []))
        result.elapsed_ms=int((time.monotonic()-started)*1000)
        result.sha256=hashlib.sha256((ARTIFACT_ROOT/result.artifact).read_bytes()).hexdigest() if result.artifact else None
        return result
    if provider == "kaggle-gpu":
        raise ToolError("Kaggle is a Learning Connector and cannot execute local media generation")
    if provider not in (None, "huggingface"):
        raise ToolError("Requested provider is not registered for this adapter")
    timeout=contract.timeout_seconds
    if name == "image.generate":
        prompt=_string(inputs.get("prompt"),"prompt")
        width,height=_dimensions(inputs.get("width",512),inputs.get("height",512))
        data,mime=_hf(name,{"inputs":prompt,"parameters":{"width":width,"height":height}},timeout,binary=True)
        if not mime.startswith("image/"): raise ToolError("Image provider did not return an image")
        path=_save(data,".png"); verification=_verify_png(path)
        if verification["width"] != width or verification["height"] != height:
            path.unlink(missing_ok=True); raise ToolError("Generated image dimensions differ from requested dimensions")
        result=ToolResult("verified",name,"huggingface",path.name,"image/png",path.stat().st_size,verification=verification)
    elif name in {"audio.tts","music.generate"}:
        text=_string(inputs.get("text") if name=="audio.tts" else inputs.get("prompt"),"text" if name=="audio.tts" else "prompt")
        data,mime=_hf(name,{"inputs":text},timeout,binary=True)
        if mime not in {"audio/wav","audio/x-wav","application/octet-stream"}: raise ToolError("Audio provider did not return a WAV-compatible payload")
        path=_save(data,".wav"); verification,duration=_verify_wav(path)
        result=ToolResult("verified",name,"huggingface",path.name,"audio/wav",path.stat().st_size,duration,verification=verification)
    elif name == "video.generate":
        prompt=_string(inputs.get("prompt"),"prompt")
        data,mime=_hf(name,{"inputs":prompt},timeout,binary=True)
        if mime not in {"video/mp4","application/octet-stream"}: raise ToolError("Video provider did not return an MP4 payload")
        path=_save(data,".mp4"); verification=_verify_mp4(path)
        result=ToolResult("verified",name,"huggingface",path.name,"video/mp4",path.stat().st_size,verification=verification,reason="Container verified; duration/frame count requires ffprobe at deployment")
    elif name == "audio.transcribe":
        encoded=_string(inputs.get("audio_base64"),"audio_base64",MAX_ARTIFACT_BYTES)
        import base64
        try: audio=base64.b64decode(encoded,validate=True)
        except Exception as exc: raise ToolError("audio_base64 is invalid") from exc
        if not audio or len(audio)>MAX_ARTIFACT_BYTES: raise ToolError("Audio input is empty or too large")
        data,mime=_hf(name,audio,timeout,binary=False)
        parsed=json.loads(data.decode("utf-8"))
        transcript=parsed.get("text") if isinstance(parsed,dict) else None
        if not isinstance(transcript,str) or not transcript.strip(): raise ToolError("ASR provider returned no transcript")
        result=ToolResult("verified",name,"huggingface",output={"text":transcript},verification={"valid":True,"non_empty_transcript":True})
    elif name == "translate":
        text=_string(inputs.get("text"),"text")
        target=_string(inputs.get("target_language"),"target_language",32).casefold()
        if target not in {"sw","swahili"}:
            raise ToolError("The configured Helsinki-NLP/opus-mt-en-sw adapter currently supports target_language=sw only")
        data,mime=_hf(name,{"inputs":text},timeout,binary=False)
        parsed=json.loads(data.decode("utf-8"))
        translated=parsed[0].get("translation_text") if isinstance(parsed,list) and parsed and isinstance(parsed[0],dict) else None
        if not isinstance(translated,str) or not translated.strip(): raise ToolError("Translation provider returned no translated text")
        result=ToolResult("verified",name,"huggingface",output={"text":translated,"target_language":target},verification={"valid":True,"non_empty_translation":True,"target_language_requested":target})
    else:
        raise ToolError("No executable adapter is registered")
    result.elapsed_ms=int((time.monotonic()-started)*1000)
    result.model_id=_model_for(name) if name in MODEL_ENV else None
    if result.artifact:
        artifact_path=(ARTIFACT_ROOT/result.artifact).resolve()
        if ARTIFACT_ROOT not in artifact_path.parents or not artifact_path.is_file():
            raise ToolError("Generated artifact path is missing or outside the artifact root")
        result.sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    if result.provider=="huggingface":
        result.gpu_telemetry={
            "gpu_used":None,
            "gpu_name":None,
            "telemetry_source":"remote_provider_not_exposed",
            "observation":"Hugging Face Inference did not expose the provider GPU device to this caller",
        }
    return result

def execute_tool_dict(name: str, inputs: dict[str, Any], *, provider: str | None = None) -> dict[str, Any]:
    try:
        result=execute_tool(name,inputs,provider=provider)
        payload={k:v for k,v in result.__dict__.items() if v is not None}
        payload["verified"] = result.status == "verified"
        return payload
    except ToolError as exc:
        return {"status":"unknown","tool":name,"provider":provider or CONTRACTS.get(name,ToolContract(name,{}, {},1,"","unknown","")).provider,
                "verified":False,"reason":str(exc)}
