---
title: VORAX Runtime Service
sdk: docker
app_port: 7860
---

# VORAX Runtime Service

This is a **Docker Space service**, not a model repository. It runs the repository's shared `vorax/` package and V-Lang interpreter.

It exposes:
- `GET /health`
- `POST /vorax/x` — one authenticated Input X
- `POST /vorax/verify` — trusted V-Lang validation and Runtime-only pending Vault write
- `POST /vorax/learn` — ask the separate Teacher Service, then validate the returned method

Authentication uses the Kaggle Child identity/key plus server-side GitHub Home and Vault credentials. Requests may pass `child_id` and `api_key` in JSON as documented; `x-child-id` and `x-child-key` headers are preferred.

## Trust boundaries

- The Runtime executes the repository's trusted V-Lang interpreter and reruns every declared test.
- A Child's claimed verification is never trusted.
- A passing candidate is stored only under `vlang/pending/` in the existing private Dataset Vault.
- A Home PR is opened after the pending Vault write. Only a later Home sync can make a Home-merged method executable.
- The Runtime does not merge PRs and does not certify GO.
- Artifact SHA-256 is returned only when an actual verified artifact result includes a digest. No placeholder digest is created.

## Weight and media policy

No model weights are downloaded or hosted here. Model learning requests go to the separate Teacher Service through remote Inference Providers. No Diffusers or Transformers local generation pipeline is installed. This service does not claim Kaggle T4 use.
