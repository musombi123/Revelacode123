---
license: apache-2.0
tags:
  - symbolic-reasoning
  - t-lang
  - deterministic-computation
  - model-shaped-api
pipeline_tag: text-generation
---

# RevelaCode Legend — T-Lang symbolic engine

**Legend is not a neural network and contains no model weights.** This model-repository-shaped package exposes a deterministic, verified template composition engine. Its "expansion" is through T-Lang procedures accepted into GitHub Home after automated checks—not gradient training.

- Architecture label: `LegendForReasoning`
- T-Lang version: `1.0`
- Runtime: symbolic interpreter, no model required for supported tasks
- Unknown or unsupported tasks return `verified: false`; Legend must not guess.

## API contract

Child API `POST /generate`:

```json
{"inputs":"triangle area base 10 height 5"}
```

Response:

```json
{"answer":25,"template_used":"geometry.triangle_area","verified":true,"status":"verified","message":"Computed using a locally verified procedure."}
```

Structured input is also supported:

```json
{"inputs":{"task":"calculate triangle area from base and height","inputs":{"base":10,"height":5}}}
```

This is a custom HTTP inference interface. It is **not** a native Hugging Face Transformers `pipeline()` implementation because Legend has no Transformers weights or Transformers model class. `legend_engine.py` is a thin API client.
