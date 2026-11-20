---
title: VORAX Runtime Service
sdk: docker
app_port: 7860
---

# VORAX Runtime Service

This is a **public Docker Space service**, not a model repository. Runtime routes that accept Child work require VORAX Child authentication; the existing Vault remains a private Dataset. It runs the repository's shared `vorax/` package and V-Lang interpreter. It does not bundle Home's `templates/library/` method bodies; it reads only `templates/library/index.json` summaries for duplicate detection.

It exposes:
- `GET /health`
- `POST /vorax/x` — one authenticated Input X
- `POST /vorax/verify` — trusted V-Lang validation and Runtime-only pending Vault write
- `POST /vorax/learn` — ask the separate Teacher Service, then validate the returned method

Authentication uses the Kaggle Child identity/key plus server-side GitHub Home and Vault credentials. Runtime also requires the private `VORAX_TEACHER_ATTESTATION_KEY` shared with Teacher; Kaggle never receives this signing key. Requests may pass `child_id` and `api_key` in JSON as documented; `x-child-id` and `x-child-key` headers are preferred.

## Trust boundaries

- The Runtime executes the repository's trusted V-Lang interpreter and reruns every declared test.
- A Child's claimed verification is never trusted.
- Runtime verifies the Teacher's HMAC-signed remote response, timestamp, model ID, provider flags and response hash, then checks that the Child's submitted method is exactly the method in that signed response.
- A passing candidate is stored only under `vlang/pending/` in the existing private Dataset Vault.
- A Home PR includes the proposed V-Lang method and a metadata-only index update. Only Home validates/merges it.
- After merge, Runtime may promote the candidate already in Vault pending to `vlang/verified/` when Home's index contains the same ID, task metadata and canonical method SHA-256. Runtime never downloads the method body from Home.
- The Runtime does not merge PRs and does not certify GO.
- Artifact SHA-256 is returned only when an actual verified artifact result includes a digest. No placeholder digest is created.

The authenticated `GET /vorax/home-index` route exposes only method IDs, domains, task summaries, triggers and canonical method SHA-256 digests. It never returns executable V-Lang method bodies.

## Weight and media policy

No model weights are downloaded or hosted here. Model learning requests go to the separate Teacher Service through remote Inference Providers. No Diffusers or Transformers local generation pipeline is installed. This service does not claim Kaggle T4 use.
