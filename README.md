---
title: VORAX Child
sdk: docker
app_port: 8000
---

# VORAX — One Input X

VORAX is a general-purpose execution engine, not a model. It classifies one input X, resolves verified V-Lang procedures from Home, calls registered providers, and reports success only when execution evidence is independently verified.

## Canonical runtime

- `vorax/` is the single shared runtime used by hosted Children.
- `vorax/api.py` exposes `GET /health`, `POST /vorax/x`, and authenticated `POST /v1/vault/sync`.
- Hugging Face Child and AWS Child are hosting adapters around the same package.
- GitHub repository `musombi123/Revelacode123` is the trusted Home. The HF Dataset Vault is a backup mirror only.
- No model package, weights, `config.json`, `architectures`, or `model_type` is part of this runtime.

## Hugging Face deployment

Target Docker Space: `musombi123/revelacode-vorax-child`. Add GitHub secret `HF_SPACE_TOKEN` with permission to create/update that Space. Workflow `.github/workflows/deploy-hf-child.yml` uploads the shared runtime.

Configure these Space secrets:
- `VORAX_CHILD_ID=hf-child`
- `VORAX_CHILD_API_KEY`
- `LEGEND_HOME_REPO=musombi123/Revelacode123`
- `LEGEND_GITHUB_TOKEN` (read-only Home access)
- `HF_TOKEN` or `HF_INFERENCE_TOKEN` for approved inference calls
- `HF_VAULT_TOKEN` (write access to the Dataset Vault only)
- `HF_VAULT_REPO=musombi123/revelacode-vorax-vault`

## GPU and storage: actual platform limits

This service is a Docker/FastAPI Space. Hugging Face ZeroGPU is shared RTX Pro 6000 hardware for Gradio Spaces, not a free T4 option for Docker. If a Docker Space needs a local GPU, request a paid T4 small in Space Settings (currently listed at USD 0.40/hour). The runtime reports local GPU telemetry only when `nvidia-smi` can observe a device. Current HF Inference Provider calls use remote provider compute; they do not prove local Space GPU usage.

The Dataset Vault is created as a private Dataset repository on first authenticated sync. Hugging Face does not grant 1TB private storage to a free account; confirm plan quota or upgrade before relying on 1TB. The Vault does not replace GitHub Home's PR/CI validation gate.

## Vault sync

After the Space is deployed and its secrets configured, call `POST /v1/vault/sync` with `x-child-id` and `x-child-key`. It fetches canonical V-Lang methods from GitHub Home, independently verifies local PNG/WAV/MP4 artifacts, and uploads the method/artifact mirror plus a manifest to the Dataset Vault. It never mirrors unverified artifacts.

## Local checks

```bash
python -m pip install -r requirements.txt
python scripts/verify_library.py
python -m unittest discover -s tests -v
python -m compileall -q vorax scripts
```

RevelaAI is not connected or modified. GO / NO-GO remains BLOCKED until live Child health, actual approved GPU telemetry, confirmed Vault quota, real PNG/WAV/MP4 uploads and Home mirror proofs are collected.
