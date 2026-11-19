# REVELACODE VORAX

**VORAX — Verified Operations, Reasoning, And eXpansion** — is a bounded execution engine. It does not claim to be a neural model. It classifies one user input, checks the shared Home library for a verified V-Lang method, executes registered tools where required, and returns success only after runtime verification.

## The Input X contract

Every task enters through one endpoint:

`POST /vorax/x`

```json
{"x":"hello, who are you"}
```

The caller supplies only `x`. VORAX classifies the task, selects a Home template, resolves its registered tool calls, and returns either `verified` or `unknown`. Callers do not select models or modality-specific endpoints.

Tool-backed requests require the registered child or admin authentication headers:
- `x-child-id`
- `x-child-key`

## Execution sequence

1. **Classify X:** deterministic rules identify greeting, image, logo, music, video, speech, translation, code, 3D, text, instructions, or general tasks.
2. **Check Home:** the current Home catalogue is refreshed periodically. A V-Lang `input.classifier` method classifies X, then VORAX matches the canonical method ID.
3. **Execute:** pure methods run in the bounded interpreter. Tool methods use the server-side registry; templates cannot select arbitrary providers, URLs, shell commands, or executable source.
4. **Verify:** an execution is successful only when the procedure completes and every tool result explicitly returns `status=verified` and `verified=true`.
5. **Devour on a miss:** VORAX performs bounded public metadata discovery across Hugging Face, GitHub, arXiv, and a general web search endpoint. When a teacher is configured, it can extract a candidate V-Lang method and submit it as a Home pull request. A submitted proposal is not treated as a merged Home capability.

The discovery layer downloads no model weights. Search results and teacher output are untrusted input. Home validates the V-Lang schema, tool allowlist, provenance, and executable test vectors before merging a child proposal.

## Current Home methods and real limits

The Home seed library currently contains V-Lang methods for:
- VORAX identity/greeting (deterministic verified text)
- image and logo generation (registered image tool)
- music generation (registered music tool)
- video generation (registered video tool)
- speech synthesis (registered TTS tool)
- English-to-Swahili translation (registered translation tool)
- bounded arithmetic expression execution (restricted code tool)

A template's contract tests use a deterministic test stub to verify its tool-call structure. **That does not create or verify a real media file.** At runtime, a provider must return a real file and the adapter must verify it. Missing credentials, unavailable models, unsupported output formats, or failed verification produce `unknown`.

The current code tool evaluates bounded arithmetic expressions only. It is not a general Python/JavaScript code generator or execution sandbox. No 3D generation adapter or general-purpose text-generation adapter is registered yet. Those tasks remain `unknown` until implemented and tested.

## Teacher configuration

The optional teacher is an OpenAI-compatible chat-completions endpoint. It can be Gemini, a compatible hosted model, or another compatible service; VORAX does not assume a particular teacher.

Set these server-side variables on a child:
- `LEGEND_TEACHER_BASE_URL`
- `LEGEND_TEACHER_API_KEY`
- `LEGEND_TEACHER_MODEL`
- `LEGEND_GITHUB_TOKEN` with the required Home contents and pull-request permissions
- `VORAX_AUTO_LEARN=true` to enable opportunistic teacher proposals on unsupported X

Teacher or Home submission failure never replaces the current `unknown` result. A method becomes shared only after the Home gate accepts and merges its pull request, then the child refreshes Home.

## V-Lang and legacy compatibility

New methods use `vlang/1` and `.vlang` files. The reader continues to accept existing `tlang/1` and `.tlang` records so stored methods are not lost. New Home submissions are serialized as V-Lang.

The language is data-only. Its `CALL TOOL` instruction is restricted to the fixed server registry and the exact argument schema for each registered tool. It cannot make arbitrary HTTP requests or execute arbitrary code.

## Local verification

```bash
python -m pip install -r requirements.txt
python scripts/verify_library.py
python -m unittest discover -s tests -v
python -m compileall -q legend_core legend_api.py scripts
uvicorn legend_api:app --host 0.0.0.0 --port 8000
```

Health: `GET /v1/health`

V-Lang specification: `GET /v1/vlang/spec`

Tool registry metadata: `GET /v1/tools`

The engine intentionally prefers an explicit `unknown` over a plausible but unverified answer.
