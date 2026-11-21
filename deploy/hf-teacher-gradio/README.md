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

This is the Teacher API hosted through the Gradio SDK on an eligible free ZeroGPU Space. The API contract is preserved; the Gradio launch wrapper does not replace the Teacher logic.

## API contract

- `GET /health`
- `GET /teacher/discover?query=...&limit=...` — Hub models, Spaces, Datasets and Inference Providers catalogue metadata only.
- `POST /teacher/ask` — authenticated remote Inference Providers request.

## VORAX learning boundary

The Teacher is not VORAX and does not certify knowledge. It proposes a candidate procedure from remote model reasoning. The separate VORAX Runtime validates the exact candidate using the shared V-Lang interpreter and declared tests. A passing proposal remains pending in the existing private Dataset Vault until GitHub Home accepts the matching method through its normal review/merge path.

## Weight and compute policy

- No model weights are downloaded, cached, trained or hosted.
- No local inference model is loaded.
- Remote inference is free-provider-only by default; billable providers remain disabled.
- The ZeroGPU platform marker is never invoked. Teacher metadata and HTTP processing run on the Space's ordinary web process; VORAX does not request GPU work.
- Kaggle T4 is not used.

## Secrets

Configure these as Space secrets, never as repository files:

- `HF_TOKEN` — Hugging Face Inference Providers permission.
- `HF_READ_TOKEN` — Hub metadata read permission.
- `VORAX_CHILD_ID=kaggle-child`
- `VORAX_KAGGLE_CHILD_API_KEY`
- `VORAX_TEACHER_ATTESTATION_KEY` — shared with Runtime only; never put this key in Kaggle.
- `VORAX_ALLOW_BILLABLE_INFERENCE=false`

The Teacher never writes to the VORAX Vault.
