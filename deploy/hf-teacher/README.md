---
title: VORAX Teacher Service
sdk: docker
app_port: 7860
---

# VORAX Teacher Service

This is a **public Docker Space service**, not a model repository. API inference is protected by VORAX Child authentication; the Space stores no credentials in its source tree.

It exposes:
- `GET /health`
- `GET /teacher/discover?query=...` — Hugging Face Hub metadata only (`id`, `tags`, `downloads`, `likes`)
- `POST /teacher/ask` — authenticated remote Inference Providers chat completion

## Weight policy

This service never downloads, caches, trains, or hosts model weights. It does not import Diffusers or Transformers pipelines and does not call `snapshot_download`. It only calls the Hub metadata API and the hosted Inference Providers router.

Teacher secrets:
- `HF_TOKEN` — token with Inference Providers permission
- `HF_READ_TOKEN` — optional Hub metadata-read token
- `VORAX_KAGGLE_CHILD_API_KEY`
- `VORAX_CHILD_ID=kaggle-child`

The Teacher never writes to the VORAX Vault. Only the Runtime Service may write a Runtime-verified pending method.

All inference responses identify the requested model, provider (or `unknown` if not supplied by the upstream response), remote inference status, and `kaggle_t4_used=false`.
