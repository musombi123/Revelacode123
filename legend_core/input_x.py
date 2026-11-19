"""VORAX Input X: one deterministic intake, Home lookup, and bounded source discovery."""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any

from .engine import compose
from .vlang import execute, TemplateError

MAX_X_CHARS = 12_000
TASK_TEMPLATES = {
    "greeting": "greeting.identity",
    "image": "image.generate",
    "logo": "logo.generate",
    "music": "music.generate",
    "video": "video.generate",
    "code": "code.execute",
    "speech": "speech.synthesize",
    "translation": "translation.swahili",
}
TASK_TOOLS = {
    "image": "image.generate",
    "logo": "image.generate",
    "music": "music.generate",
    "video": "video.generate",
    "code": "code.execute",
    "speech": "audio.tts",
    "translation": "translate",
}


def classify_input_x(value: str, catalog: list[dict] | None = None) -> dict[str, str | None]:
    """Run the canonical Home V-Lang classifier; there is no neural classifier fallback."""
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_X_CHARS:
        raise ValueError(f"x must be non-empty text of at most {MAX_X_CHARS} characters")
    catalog = catalog or []
    classifier = next((item for item in catalog if item.get("id") == "input.classifier"), None)
    if classifier is None:
        return {"task_type": "unknown", "template_id": None, "tool": None,
                "classifier_template": None}
    try:
        task_type, _ = execute(classifier, {"x": value})
    except TemplateError:
        return {"task_type": "unknown", "template_id": None, "tool": None,
                "classifier_template": "input.classifier"}
    if not isinstance(task_type, str):
        task_type = "unknown"
    template_id = TASK_TEMPLATES.get(task_type)
    tool = TASK_TOOLS.get(task_type)
    # code.execute is only a restricted arithmetic evaluator, never a general code generator.
    is_expression = bool(re.fullmatch(r"[0-9\s().+*/%-]+", value.strip())) and any(ch.isdigit() for ch in value)
    if task_type == "code" and not is_expression:
        template_id = None
        tool = None
    return {"task_type": task_type, "template_id": template_id, "tool": tool,
            "classifier_template": "input.classifier"}


def handle_input_x(value: str, catalog: list[dict], *, tool_executor=None) -> dict[str, Any]:
    """Resolve one X against Home and execute only a verified V-Lang procedure."""
    classification = classify_input_x(value, catalog)
    task_type = classification["task_type"]
    template_id = classification["template_id"]
    if not template_id:
        return {
            "status": "unknown", "answer": None, "classification": classification,
            "message": "VORAX has no verified Home method for this task yet.",
        }
    template = next((item for item in catalog if item.get("id") == template_id), None)
    if template is None:
        return {
            "status": "unknown", "answer": None, "classification": classification,
            "message": "No matching verified method is present in the current Home catalogue.",
        }
    if task_type == "greeting":
        inputs = {"query": value}
    elif task_type == "code":
        inputs = {"source": value}
    elif task_type == "speech":
        inputs = {"text": value}
    elif task_type == "translation":
        inputs = {"text": value}
    else:
        inputs = {"prompt": value}
    result = compose(catalog, template_id=template_id, inputs=inputs, tool_executor=tool_executor)
    if result.get("status") != "verified":
        return {
            "status": "unknown", "answer": None, "classification": classification,
            "template_id": template_id, "tool": classification["tool"],
            "reason": result.get("message", "The method or its tool did not verify."),
        }
    response = {
        "status": "verified", "answer": result.get("answer"),
        "classification": classification, "template_id": template_id,
        "tool": classification["tool"], "proof": result.get("proof", []),
    }
    answer = result.get("answer")
    if isinstance(answer, dict) and isinstance(answer.get("artifact"), str):
        response["artifact_url"] = f"/v1/artifacts/{answer['artifact']}"
    return response


class _SearchLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items: list[dict[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "result__a" in attrs.get("class", ""):
            self._href = attrs.get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            title = " ".join(" ".join(self._text).split())
            url = self._href
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme == "https" and parsed.netloc and title:
                self.items.append({"title": html.unescape(title), "url": url})
            self._href = None
            self._text = []


def _read_json(url: str, *, headers: dict | None = None, timeout: int = 3):
    request = urllib.request.Request(url, headers={"User-Agent": "VORAX-Input-X/1.0", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(512_000)
    return json.loads(raw.decode("utf-8"))


def discover_method_sources(task_type: str, query: str, *, limit: int = 5) -> dict[str, Any]:
    """Search bounded public metadata sources; never downloads model weights or runs source code."""
    if task_type not in {"greeting", "image", "logo", "music", "video", "3d", "text", "code", "instructions", "speech", "translation", "general"}:
        task_type = "general"
    q = " ".join(query.split())[:500]
    search_term = f"{task_type} method how to implement {q}"
    count = max(1, min(int(limit), 5))
    sources: list[dict[str, str]] = []
    errors: list[str] = []

    # Hugging Face model cards/metadata, not model weights.
    try:
        url = "https://huggingface.co/api/models?" + urllib.parse.urlencode({
            "search": task_type, "limit": count, "sort": "downloads", "direction": -1,
        })
        for item in _read_json(url)[:count]:
            model_id = item.get("id")
            if model_id:
                sources.append({
                    "source": "huggingface", "title": model_id,
                    "url": "https://huggingface.co/" + model_id,
                    "description": ", ".join((item.get("tags") or [])[:8]),
                })
    except Exception as exc:
        errors.append("huggingface:" + type(exc).__name__)

    # Public GitHub repository metadata.
    try:
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({
            "q": search_term, "sort": "stars", "order": "desc", "per_page": count,
        })
        payload = _read_json(url, headers={"Accept": "application/vnd.github+json"})
        for item in payload.get("items", [])[:count]:
            sources.append({
                "source": "github", "title": item.get("full_name", ""),
                "url": item.get("html_url", ""),
                "description": (item.get("description") or "")[:300],
            })
    except Exception as exc:
        errors.append("github:" + type(exc).__name__)

    # arXiv papers are method references, not executable capabilities.
    try:
        term = urllib.parse.quote(f'all:"{task_type}" AND all:"method"')
        url = f"https://export.arxiv.org/api/query?search_query={term}&start=0&max_results={count}"
        request = urllib.request.Request(url, headers={"User-Agent": "VORAX-Input-X/1.0"})
        with urllib.request.urlopen(request, timeout=3) as response:
            xml = response.read(512_000)
        root = ET.fromstring(xml)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns)[:count]:
            title = " ".join((entry.findtext("a:title", default="", namespaces=ns)).split())
            link = next((node.attrib.get("href", "") for node in entry.findall("a:link", ns)
                         if node.attrib.get("rel") == "alternate"), "")
            if title and link.startswith("https://"):
                sources.append({"source": "arxiv", "title": title, "url": link, "description": ""})
    except Exception as exc:
        errors.append("arxiv:" + type(exc).__name__)

    # General web search for public tutorials and official documentation.
    try:
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": search_term})
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 VORAX-Input-X/1.0"})
        with urllib.request.urlopen(request, timeout=3) as response:
            page = response.read(512_000).decode("utf-8", "replace")
        parser = _SearchLinks()
        parser.feed(page)
        for item in parser.items[:count]:
            sources.append({"source": "web", **item, "description": ""})
    except Exception as exc:
        errors.append("web:" + type(exc).__name__)

    unique = []
    seen = set()
    for item in sources:
        url = item.get("url", "")
        if url.startswith("https://") and url not in seen:
            seen.add(url)
            unique.append(item)
        if len(unique) >= count * 4:
            break

def fetch_method_evidence(sources: list[dict[str, str]], *, limit: int = 3) -> list[dict[str, str]]:
    """Fetch small public method notes from fixed, allowlisted source APIs."""
    selected = []
    seen_sources = set()
    for item in sources:
        kind = item.get("source")
        if kind in {"huggingface", "github", "arxiv"} and kind not in seen_sources:
            selected.append(item)
            seen_sources.add(kind)
        if len(selected) >= max(1, min(limit, 3)):
            break

    evidence = []
    for item in selected:
        kind = item.get("source")
        title = item.get("title", "")
        try:
            excerpt = ""
            if kind == "huggingface" and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", title):
                url = "https://huggingface.co/" + title + "/raw/main/README.md"
                request = urllib.request.Request(url, headers={"User-Agent": "VORAX-Input-X/1.0"})
                with urllib.request.urlopen(request, timeout=2) as response:
                    excerpt = response.read(24_000).decode("utf-8", "replace")
            elif kind == "github" and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", title):
                url = "https://api.github.com/repos/" + title + "/readme"
                payload = _read_json(url, headers={"Accept": "application/vnd.github+json"}, timeout=2)
                if payload.get("encoding") == "base64" and isinstance(payload.get("content"), str):
                    import base64
                    excerpt = base64.b64decode(payload["content"]).decode("utf-8", "replace")
            elif kind == "arxiv":
                parsed = urllib.parse.urlparse(item.get("url", ""))
                match = re.fullmatch(r"/abs/([A-Za-z0-9.\-/]+)", parsed.path)
                if parsed.hostname == "arxiv.org" and match:
                    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": match.group(1)})
                    request = urllib.request.Request(url, headers={"User-Agent": "VORAX-Input-X/1.0"})
                    with urllib.request.urlopen(request, timeout=2) as response:
                        root = ET.fromstring(response.read(128_000))
                    ns = {"a": "http://www.w3.org/2005/Atom"}
                    entry = root.find("a:entry", ns)
                    excerpt = entry.findtext("a:summary", default="", namespaces=ns) if entry is not None else ""
            excerpt = " ".join(excerpt.split())[:6000]
            if excerpt:
                evidence.append({"source": kind, "title": title, "url": item.get("url", ""),
                                 "excerpt": excerpt, "trust": "untrusted_public_reference"})
        except Exception:
            # Search results remain visible as references even when a source cannot be fetched.
            continue
    return evidence

    evidence = fetch_method_evidence(unique)
    return {"status": "sources_found" if unique else "no_sources", "query": search_term,
            "sources": unique, "method_evidence": evidence,
            "source_errors": errors, "weights_downloaded": False}
