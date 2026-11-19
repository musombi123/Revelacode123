---
title: VORAX Child
sdk: docker
app_port: 8000
---

# VORAX Child — Docker Space

This is an execution Child, not a model repository. It serves `GET /health` and `POST /vorax/x`.

Configure Space secrets: `VORAX_CHILD_ID=hf-child`, `VORAX_CHILD_API_KEY`, `LEGEND_HOME_REPO=musombi123/Revelacode123`, read-only `LEGEND_GITHUB_TOKEN`, `HF_TOKEN` for provider inference, and `HF_VAULT_TOKEN` with write access only to the private Dataset Vault. Optionally set `HF_VAULT_REPO=musombi123/revelacode-vorax-vault`.

## Hardware and GPU truth

This app uses Docker/FastAPI. Select an available paid GPU such as Nvidia T4 small in Space Settings if local GPU execution is required. Hugging Face ZeroGPU is a separate shared RTX Pro 6000 service and supports Gradio Spaces, not this Docker Space; it is not a free T4 allocation. The app reports actual local device telemetry from `nvidia-smi`. It does not claim GPU use if the device is unavailable.

The existing media adapters call HF Inference Providers remotely. Those calls use provider-side compute and are not proof that this Space's local GPU is being used.

## Vault

Authenticated `POST /v1/vault/sync` mirrors V-Lang methods from GitHub Home and independently verifies local PNG/WAV/MP4 files before uploading them to the private Dataset repo. The Vault is a backup mirror, not a replacement for GitHub's trusted merge gate. Private 1TB storage is not included automatically on a free account; confirm your quota or upgrade before relying on a 1TB Vault.
