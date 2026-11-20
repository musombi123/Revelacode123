---
title: VORAX — One Input X
sdk: docker
app_port: 8000
---

# VORAX — One Input X

VORAX is one shared runtime and one V-Lang method system. Hosting platforms are Children, not separate engines. GitHub Home is the trusted authority. RevelaAI is not connected or modified.

## Locked runtime topology

| Component | Responsibility | Honest availability |
|---|---|---|
| GitHub Actions | Sole scheduler; pings Children every 30 minutes, checks Kaggle status, starts a bounded Kaggle run when queued X exists, records evidence and finalizes the 7-day campaign | Scheduled workflow; no VORAX runtime execution on GitHub |
| Koyeb | Lightweight shared `vorax/` HTTP runtime; `GET /health`, `POST /vorax/x`; quick deterministic/text X; queues heavy X for Kaggle | Free service may scale to zero. A ping is best-effort, not an uptime guarantee |
| Kaggle | Ephemeral research worker using the same shared `vorax/` package and V-Lang; checks real NVIDIA telemetry; processes queued tasks only while its kernel session is running | Not a permanent API. The controller starts a bounded run when a queue exists; availability and quotas are Kaggle-controlled |
| Hugging Face Space | Backup Docker Child running the same shared runtime | Backup only; deploy and health must be verified separately |
| Hugging Face Dataset | Private Vault mirror of Home-merged methods, independently verified artifacts, indexes and reports | Capacity is not presumed to be 1 TB; quota must be checked in the account |

The controller cron is `17,47 * * * *`. It does not run VORAX itself. It probes Koyeb and the HF backup, checks Kaggle kernel status, and starts Kaggle only when queued work exists and no active session is observed. A Koyeb 502/503 is logged as “Koyeb asleep, retry later” rather than counted as a permanent Child failure.

## Input X routing

- Koyeb handles light deterministic/text tasks through the shared runtime.
- Heavy modalities classified as image, logo, music, video, speech/audio or 3D are placed in a private GitHub Home queue. The response is `queued` and `verification=not_executed`; queuing is not execution.
- Kaggle reads queued X while a kernel session is available, checks local GPU telemetry with `nvidia-smi`, starts the shared runtime, and records a worker report.
- Kaggle now has a **code implementation** of a local Diffusers image adapter. It installs the optional `kaggle/child/requirements-gpu.txt` only when an image request is queued, loads the approved model inside the Kaggle session, requires CUDA, checks that the UNet, text encoder and VAE are resident on the observed CUDA device, generates PNG bytes locally, and records model, seed, steps, execution time, GPU name/device, memory telemetry, SHA-256, size and image verification. There is no CPU fallback and no remote inference fallback on the Kaggle GPU route.
- **This adapter is not yet live-proven.** It has not been run in a real Kaggle GPU session in this repository workflow; no PNG is claimed, and the Children are not certified live. Its first successful execution must produce a non-blank PNG, independently re-hash it in the worker, and appear in the Vault sync proof at `artifacts/images/<sha256>.png`.
- Local GPU adapters for audio, music and video remain unimplemented and must stay `blocked_local_gpu_adapter` / `UNKNOWN`. 3D remains `UNKNOWN`. The existing default Hugging Face Inference adapters are remote services and their GPU device is not exposed to this runtime; a remote result can never be labelled Kaggle GPU execution.

Do not put passwords, API keys, private records or sensitive personal data in the GitHub X queue. The queue is stored as an issue in this private repository.

## Home trust and auto-merge policy

All Child proposals go to GitHub Home through pull requests. Children cannot merge or certify themselves.

- V-Lang proposals: the trusted-base verifier reads the PR as data, validates schema and duplicate checks, and runs declared V-Lang test vectors through the trusted interpreter and its registered contract test stub. The trusted core CI must also succeed on the exact PR head SHA before auto-merge.
- Data-only improvements: only allowlisted `docs/vorax/**`, V-Lang index JSON, `manifest/**/*.json` and `reports/**/*.json` can enter the data gate. Home checks paths, JSON structure, active HTML and credential-like fields, then waits for exact-head CI.
- Executable code, Python/JavaScript/shell, dependencies, Dockerfile, project configuration, deployment workflows, templates/actions, secrets and permission changes are not eligible for data auto-merge. They require trusted human review.

Both PR gates check out the trusted base revision. They do not execute scripts supplied by a Child PR. A mixed PR containing code and data is rejected from the data-only gates.

## Deploy and configure

Deployment workflows are manual and run only from `main`. Merge this implementation PR before dispatching them.

### Koyeb

Workflow: `.github/workflows/deploy-koyeb-child.yml`

Required GitHub Actions secrets:
- `KOYEB_API_TOKEN`
- `VORAX_KOYEB_CHILD_API_KEY`
- `VORAX_HOME_READ_TOKEN` — read access to this private Home repository
- `VORAX_HOME_WRITE_TOKEN` — create/update queue issues and submit Child proposal PRs

Optional secrets/variables:
- `HF_VAULT_TOKEN` — write access to the Dataset Vault
- `HF_INFERENCE_TOKEN` — remote HF Inference access; this is not local Kaggle GPU use
- `LEGEND_TEACHER_API_KEY`
- Variable `LEGEND_TEACHER_MODEL`; optional `LEGEND_TEACHER_BASE_URL`

The Koyeb workflow creates service secrets and deploys the shared root Dockerfile. It configures `VORAX_CHILD_ROLE=koyeb`, uses temporary `/tmp` storage, and configures the `/health` check. The service can sleep on the free tier.

Set repository variable `VORAX_KOYEB_URL` to the service base URL for the controller.

### Kaggle

Workflow: `.github/workflows/deploy-kaggle-child.yml`

Required GitHub Actions secrets:
- `KAGGLE_USERNAME`
- `KAGGLE_KEY`

Variable:
- `KAGGLE_KERNEL_SLUG` — for example `revelacode-vorax-child`

The publish workflow uses `kaggle kernels push --no-run`: it publishes source and metadata but does not start a GPU session. The Self-Ping Controller starts the configured kernel when queued work exists. Optional variable `KAGGLE_ACCELERATOR` defaults to `NvidiaTeslaT4`.

Add these Kaggle Notebook secrets in the Kaggle account settings:
- `VORAX_HOME_READ_TOKEN`
- `VORAX_HOME_WRITE_TOKEN`
- `VORAX_KAGGLE_CHILD_API_KEY`

Optional Kaggle Notebook secrets:
- `VORAX_CHILD_PROPOSAL_TOKEN` (otherwise the Home write token is used)
- `HF_VAULT_TOKEN`
- `HF_INFERENCE_TOKEN` — optional remote-provider access; it does not prove Kaggle GPU use.
- `HF_MODEL_TOKEN` — optional Hugging Face Hub read token for downloading the local Diffusers image model if the selected model requires authentication.
- `VORAX_KAGGLE_IMAGE_MODEL` — optional runtime environment override; defaults to `stable-diffusion-v1-5/stable-diffusion-v1-5`.
- `LEGEND_TEACHER_API_KEY`
- `LEGEND_TEACHER_MODEL`
- `LEGEND_TEACHER_BASE_URL`

The worker installs `kaggle/child/requirements-gpu.txt` only when queued image work exists. Model weights are downloaded into the temporary Kaggle session; no persistent GPU service or persistent weight store is assumed. Set repository variables `VORAX_KOYEB_URL`, `VORAX_HF_CHILD_URL`, and `KAGGLE_KERNEL_SLUG` for the controller. The Kaggle run timeout defaults to 42,000 seconds, below the 12-hour session ceiling in the current configuration; actual availability and quota remain platform-controlled.

### Hugging Face backup Child and Vault

Workflow: `.github/workflows/deploy-hf-child.yml`

Required GitHub Actions secret: `HF_SPACE_TOKEN`, with permission to create/update the target Space.

Exact repositories:
- Docker Space: `Musombi/revelacode-vorax-child`
- Private Dataset Vault: `Musombi/revelacode-vorax-vault`

Configure the Space with `VORAX_CHILD_ID=hf-child`, `VORAX_CHILD_API_KEY`, Home read credentials, and the optional inference/teacher settings needed by that Space. For Vault writes, configure `HF_VAULT_TOKEN` and `HF_VAULT_REPO=Musombi/revelacode-vorax-vault`. A Docker Space GPU is not implied by remote HF inference.

### Start the 7-day campaign

After configuring the repository variables and secrets, open Actions → VORAX Self-Ping Controller → Run workflow → `start-campaign`. The controller records UTC start and end timestamps in a Home issue. Scheduled heartbeats finalize the campaign after seven days. The workflow also supports `queue-x`, `status`, `start-kaggle` and `stop-campaign`.

## Vault structure

The sync writes Home-merged V-Lang methods to `vlang/verified/`; pending methods remain PRs and are not promoted. Verified media is written to `artifacts/images/`, `artifacts/audio/` and `artifacts/video/` by SHA-256. Indexes, checksums and runtime/sync manifests are written under `vlang/` and `manifest/`; current verification, Devour and GO/NO-GO reports are written under `reports/`.

PNG, WAV and MP4 must pass independent format checks before upload. MP4 requires more than 10 decoded frames and positive duration; WAV must contain non-silent samples; PNG must decode and not be a single-colour blank. The sync report explicitly keeps Vault capacity unverified.

## Local verification

```bash
python -m pip install -r requirements.txt
python scripts/verify_library.py
python -m unittest discover -s tests -v
python -m compileall -q vorax scripts kaggle/child
docker build --tag revelacode-vorax:local .
```

## GO / NO-GO

**GO / NO-GO remains BLOCKED.** The merged Child-services foundation and this follow-up code branch establish deployment/configuration workflows, honest health checks, X queueing, the controller, data-only trust gates, structured Vault sync, and a code-level Kaggle local image adapter. They do not establish that the external services have been deployed or that a real Kaggle image has been generated.

Home must still collect and verify:
1. Live Koyeb, Kaggle-session and HF Child evidence.
2. Actual Kaggle local GPU execution plus a real non-empty PNG with independent SHA-256, size, model identity, CUDA device provenance and Vault path. Audio, music, video and 3D remain blocked/unknown until their own real local adapters are implemented and verified.
3. Confirmed HF Vault quota and successful sync evidence.
4. At least one Koyeb-origin and one Kaggle-origin V-Lang method independently validated and merged by Home.
5. At least 90% verified common-X coverage, the complete seven-day campaign, and green CI/security checks.

AWS is postponed. No `hf-model/` or separate runtime engine is introduced. RevelaAI remains untouched.
