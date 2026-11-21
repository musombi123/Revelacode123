---
title: VORAX Teacher Service
emoji: 🧠
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 6.32.0
app_file: app.py
python_version: "3.12"
short_description: Remote capability teacher; no local model weights
startup_duration_timeout: 30m
---

# VORAX Teacher Service

This is the Teacher API hosted through Gradio Server mode on an eligible free ZeroGPU Space. The adapter does not replace Teacher logic or alter the HTTP API.

## API contract

- `GET /health`
- `GET /teacher/discover?query=...&limit=...` — Hub models, Spaces, Datasets and Inference Providers catalogue metadata only.
- `POST /teacher/ask` — authenticated remote Inference Providers request.

## VORAX learning boundary

The Teacher is not VORAX and does not certify knowledge. It proposes a candidate procedure from remote model reasoning. The separate VORAX Runtime validates the exact candidate using the shared V-Lang interpreter and declared tests. A passing proposal remains pending in the existing private Dataset Vault until Runtime commits the matching verified method and metadata-only index to GitHub Home.

## Hosting, compute and weight policy

- This Space uses Gradio SDK with the free `zero-a10g` hosting flavor for eligible free personal accounts.
- Gradio Server mode preserves the original FastAPI endpoints and lifecycle.
- ZeroGPU requires a registered `@spaces.GPU` handler. This Space registers a private child-authenticated compatibility probe; ordinary Teacher routes never invoke it and perform no GPU work.
- The compatibility probe performs no CUDA, model loading, training or inference. It requests a one-second ZeroGPU allocation only if explicitly called with the VORAX child credential.
- Teacher metadata and HTTP routing run on the web process; no GPU time is requested for normal Teacher operations.
- No model weights are downloaded, cached, trained or hosted.
- Remote inference is free-provider-only by default; billable providers remain disabled.
- Kaggle T4 is not used.

## Secrets

Configure these as Space secrets, never as repository files:

- `HF_TOKEN` — Hugging Face Inference Providers permission.
- `HF_READ_TOKEN` — Hub metadata read permission.
- `VORAX_CHILD_ID=kaggle-child`
- `VORAX_KAGGLE_CHILD_API_KEY`
- `VORAX_TEACHER_ATTESTATION_KEY` — shared with Runtime only; never put it in Kaggle.
- `VORAX_ALLOW_BILLABLE_INFERENCE=false`

The Teacher never writes to the VORAX Vault.
