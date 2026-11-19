# V-Lang v1 — Verified Language

V-Lang is VORAX's data-only method format. A record describes a bounded procedure; it is not executable Python, JavaScript, shell, a model checkpoint, or an unrestricted prompt.

## Storage

- One JSON object per non-empty line.
- Home methods use the `.vlang` extension.
- New records use `"version":"vlang/1"`.
- The reader accepts `"version":"tlang/1"` for compatibility with existing Home records. New writes always serialize as V-Lang.

## Record shape

Every record contains exactly:

`version`, `id`, `domain`, `task`, `triggers`, `inputs`, `steps`, `result`, `tests`, `provenance`.

Input types: `number`, `integer`, `string`, `boolean`, `number_list`.

Each method must contain 1–20 steps and 2–20 executable test vectors. The verifier executes every test before a record can be accepted. Tests are evidence for the declared cases, not a mathematical proof that all possible inputs are correct.

## Deterministic operations

V-Lang includes bounded arithmetic, comparisons, list operations, string operations, unit conversions, boolean logic, identity, and `classify_intent`.

`classify_intent` is the deterministic Input X classifier used by Home's `input.classifier` method. It returns one of: `greeting`, `image`, `logo`, `music`, `video`, `3d`, `code`, `speech`, `translation`, `instructions`, `text`, or `general`.

## CALL TOOL

A method may call only a server-registered tool. Exact instruction shape:

```json
{
  "op": "call_tool",
  "tool": "image.generate",
  "args": {
    "prompt": {"input": "prompt"},
    "width": 512,
    "height": 512
  },
  "as": "artifact"
}
```

Registered tools at this version:

| Tool | Required input | Output |
|---|---|---|
| `image.generate` | `prompt` | verified PNG artifact |
| `audio.tts` | `text` | verified WAV artifact |
| `audio.transcribe` | `audio_base64` | transcript text |
| `video.generate` | `prompt` | verified MP4 artifact |
| `music.generate` | `prompt` | verified WAV artifact |
| `translate` | `text`, `target_language` | translated text |
| `code.execute` | `source` | JSON result for bounded arithmetic expressions |

No CALL TOOL step can select an arbitrary URL, provider, model ID, shell command, Python snippet, or unregistered tool. Inputs are resolved from declared method inputs, earlier step outputs, or scalar literals. The runtime checks the registry again before execution.

During Home validation, CALL TOOL tests use a deterministic contract stub. That proves the method's tool name and arguments conform to the contract; it does **not** generate a media file. During live execution, the result is successful only when a real registered adapter returns `status=verified` and `verified=true`, after artifact verification.

## Failure semantics

- Unknown tool, invalid input, missing provider, timeout, malformed response, missing artifact, or failed verification means the method fails.
- A failed method returns `UNKNOWN`; it must not invent a text answer or claim an artifact exists.
- Teacher output and internet sources are untrusted. VORAX extracts a candidate, validates it and submits it to Home. A submitted pull request is not a merged method.
- Model metadata, docs and tutorials are references. They do not become a capability until a V-Lang method and its required runtime tool pass verification.

## Home acceptance

Home re-runs the trusted V-Lang parser, executable test vectors, tool allowlist checks, provenance rules and duplicate checks before accepting a child proposal. Existing T-Lang records remain readable during migration; new proposals are written as `.vlang`.
