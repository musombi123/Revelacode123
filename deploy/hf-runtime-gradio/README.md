---
title: VORAX Runtime Service
emoji: ⚙️
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: 6.32.0
app_file: app.py
python_version: "3.12"
short_description: Trusted V-Lang runtime; Home-approved knowledge only
startup_duration_timeout: 30m
---

# VORAX Runtime Service

This is the VORAX Runtime Child, hosted through Gradio Server mode on an eligible free ZeroGPU Space. Original FastAPI route contracts and the shared `vorax/` package are preserved.

## API contract

- `GET /health`
- `POST /vorax/x` — one authenticated Input X.
- `POST /vorax/verify` — verify a Teacher-signed candidate, run V-Lang tests, and submit only a pending proposal.
- `POST /vorax/learn` — ask the separate Teacher, then independently validate its exact candidate.
- `POST /vorax/vault/reconcile` — reconcile Vault pending entries against Home's metadata index.
- `GET /vorax/home-index` — authenticated metadata-only Home index; never returns method bodies.

## Knowledge is brought Home

VORAX is not a model and is not limited to model reasoning:

1. Teacher proposes a candidate procedure. A model response is only an untrusted proposal.
2. Runtime verifies the signed Teacher response, checks provenance and executes every declared test through the shared V-Lang interpreter.
3. Passing candidates are written only to `vlang/pending/` in the existing private Dataset Vault.
4. Runtime creates an isolated `vorax/proposals/` branch containing the verified method body and matching metadata-only index, then opens a pull request against Home's configured base branch. It never updates `main` directly.
5. A pending proposal remains in Vault until its exact Home pull request is closed with `merged=true` into the configured base branch. Runtime verifies the canonical GitHub PR record.
6. Runtime then confirms the exact method ID, domain, task, triggers and canonical SHA-256 in Home's merged metadata-only index before promoting the existing Vault-pending bytes.

Runtime never downloads executable method bodies from Home. It loads only the method body already stored in the Vault after both the merged-PR proof and exact Home metadata digest are confirmed. Unmerged, closed-without-merge, wrong-base, unavailable or conflicting proposals remain pending. Runtime never force-pushes or writes directly to Home `main`.

## Hosting, compute and storage policy

- This Space uses Gradio SDK with the free `zero-a10g` hosting flavor for eligible free personal accounts.
- Gradio Server mode preserves the original FastAPI routes and registers Runtime startup Vault/Home reconciliation before serving.
- ZeroGPU requires a registered `@spaces.GPU` handler. This Space registers a private child-authenticated compatibility probe; ordinary Runtime routes never invoke it and perform no GPU work.
- The compatibility probe performs no CUDA, model loading, training or inference. It requests a one-second ZeroGPU allocation only if explicitly called with the VORAX child credential.
- V-Lang procedures, Home-index reads and Vault reconciliation use CPU/network/storage operations.
- No model weights or local reasoning model are included.
- `/tmp` is ephemeral. Durable V-Lang knowledge remains in the existing private Dataset Vault; GitHub Home remains the approval/source-of-truth layer.
- Free-provider-only policy remains enabled. Billable inference is not enabled by this Space.

## Secrets

Configure these as Space secrets, never as repository files:

- `VORAX_CHILD_ID=kaggle-child`
- `VORAX_CHILD_ROLE=hf-runtime`
- `VORAX_KAGGLE_CHILD_API_KEY`
- `VORAX_HOME_REPO=musombi123/Revelacode123`
- `VORAX_HOME_BRANCH=main`
- `VORAX_HOME_READ_TOKEN`
- `VORAX_HOME_WRITE_TOKEN`
- `HF_VAULT_TOKEN` — read/write access to the existing private Dataset only.
- `HF_VAULT_REPO=Musombi/revelacode-vorax-vault`
- `HF_INFERENCE_TOKEN`
- `VORAX_TEACHER_ATTESTATION_KEY`
- `VORAX_TEACHER_URL=https://musombi-vorax-teacher-service.hf.space`
- `VORAX_ALLOW_BILLABLE_INFERENCE=false`

The Teacher attestation key is shared only between Teacher and Runtime, never with Kaggle.
