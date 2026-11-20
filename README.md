---
title: VORAX — One Input X
sdk: docker
app_port: 8000
---

# VORAX — One Input X

VORAX is one shared runtime and one V-Lang method system. Hosting platforms are Children, not separate engines. GitHub Home is the trusted authority. RevelaAI is not connected or modified.

## Locked runtime topology

| Component | Responsibility | Phase 1 status |
|---|---|---|
| GitHub Actions | Release-gated controller; queues X, starts Kaggle on demand, and records worker/Vault evidence | Controller configuration is present; CI runner allocation is currently blocked |
| Kaggle (`musombiwilliam`) | Ephemeral GPU worker using the shared `vorax/` runtime; reads queued X, records actual GPU telemetry, then exits | **Selected first.** Not an always-on API. Free GPU quota and availability are account/session controlled |
| Hugging Face Dataset (`Musombi/revelacode-vorax-vault`) | Private Vault for Home-merged V-Lang methods, independently verified artifacts, indexes and reports | **Selected first.** Separate storage resource; no live compute is implied |
| Hugging Face Docker Space (`Musombi/revelacode-vorax-child`) | Optional backup HTTP Child using the shared runtime | Parked until HF plan/eligibility is confirmed; Docker compute Spaces require PRO on personal accounts |
| Koyeb | Optional lightweight HTTP router for quick X, with heavy X queued to Kaggle | Parked for a later hosting review; do not deploy in Phase 1 |

The controller cron is `17,47 * * * *`. It does not run VORAX itself on the GitHub runner. Before executing, it resolves the latest published GitHub Release and checks out that tag; the Kaggle kernel is pushed from the same immutable tag and verifies the exact checkout SHA. In Phase 1 it checks Kaggle kernel status and starts a bounded run only when queued X exists and no active session is observed. Koyeb and the HF HTTP backup remain optional, unconfigured probes; neither is counted as live or required for the two-platform rollout.

## Input X routing

- Koyeb handles light deterministic/text tasks through the shared runtime.
- Heavy modalities classified as image, logo, music, video, speech/audio or 3D are placed in a private GitHub Home queue. The response is `queued` and `verification=not_executed`; queuing is not execution.
- Kaggle reads queued X while a kernel session is available, checks local GPU telemetry with `nvidia-smi`, starts the shared runtime, and records a worker report.
- Kaggle currently includes the research/queue worker and model-metadata discovery. **The local GPU image/audio/music/video/3D inference adapters are not yet implemented.** Heavy media tasks are marked `blocked_local_gpu_adapter`; the worker does not claim it generated PNG, WAV or MP4 files. The current `vorax.tools` provider adapters use Hugging Face Inference endpoints, which are remote provider execution and do not prove Kaggle GPU usage.

Do not put passwords, API keys, private records or sensitive personal data in the GitHub X queue. The queue is stored as an issue in this private repository.

## Home trust and auto-merge policy

All Child proposals go to GitHub Home through pull requests. Children cannot merge or certify themselves.

- V-Lang proposals: the trusted-base verifier reads the PR as data, validates schema and duplicate checks, and runs declared V-Lang test vectors through the trusted interpreter and its registered contract test stub. The trusted core CI must also succeed on the exact PR head SHA before auto-merge.
- Data-only improvements: only allowlisted `docs/vorax/**`, V-Lang index JSON, `manifest/**/*.json` and `reports/**/*.json` can enter the data gate. Home checks paths, JSON structure, active HTML and credential-like fields, then waits for exact-head CI.
- Executable code, Python/JavaScript/shell, dependencies, Dockerfile, project configuration, deployment workflows, templates/actions, secrets and permission changes are not eligible for data auto-merge. They require trusted human review.

Both PR gates check out the trusted base revision. They do not execute scripts supplied by a Child PR. A mixed PR containing code and data is rejected from the data-only gates.

## Deploy and configure

**Phase 1 is Kaggle + the Hugging Face Dataset Vault.** Koyeb is parked. The HF Docker backup is optional and remains off until the Hugging Face plan is confirmed.

Deployment workflows run from a **published GitHub Release** or a manual dispatch against that release tag. Every deploy checks out the exact tag SHA and calls `scripts/require_green_release.py`, which fails closed unless the tag is published, resolves to the run SHA, is contained in current `main`, and the latest exact-SHA `verify` check is completed with `success`. Dispatching from a branch is rejected. Workflow files are configuration, not evidence that an external Child is live.

Kaggle's worker receives a `vorax-release.json` pin and verifies the cloned commit SHA before starting the shared runtime. The Self-Ping Controller checks out the latest published release before running, so it cannot publish current `main` code over the released kernel. The dormant Koyeb workflow is manual-only and must not be dispatched during Phase 1.

### Koyeb — parked

Workflow: `.github/workflows/deploy-koyeb-child.yml` (manual dispatch from a release tag only). Do not configure Koyeb secrets or deploy it in Phase 1.

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

The Koyeb workflow creates service secrets and deploys the shared root Dockerfile at the exact release SHA. It configures `VORAX_CHILD_ROLE=koyeb`, uses temporary `/tmp` storage, configures the `/health` check, and disables Git branch auto-deploy after the release deployment. The service may sleep; the controller records sleep/unavailable responses rather than claiming uptime.

Set repository variable `VORAX_KOYEB_URL` to the service base URL for the controller.

### Kaggle — selected Phase 1 GPU worker

Account: [kaggle.com/musombiwilliam](https://www.kaggle.com/musombiwilliam)

Workflow: `.github/workflows/deploy-kaggle-child.yml` (published-release event or manual dispatch from a release tag). The username is fixed in the workflow as `musombiwilliam`; it is not a secret.

Required GitHub Actions secret:
- `KAGGLE_API_TOKEN` — a personal API token generated in Kaggle Account Settings → API Tokens. Use the token value, not its display name. The workflow installs Kaggle CLI `>=1.8.0` and authenticates directly from this variable. Never commit it or paste it into chat.

Optional repository variable:
- `KAGGLE_KERNEL_SLUG` — defaults to `revelacode-vorax-child`.

The publish workflow uses `kaggle kernels push --no-run`: it publishes the selected release source and its SHA pin but does not start a GPU session. The Self-Ping Controller starts the configured kernel when queued work exists, using the latest published release source. The worker refuses to clone a different commit from the release pin. A live GPU session still requires a completed Kaggle run report with actual `nvidia-smi` evidence. The accelerator defaults to `NvidiaTeslaP100`, matching Kaggle's documented free GPU baseline; set `KAGGLE_ACCELERATOR` only when the account exposes another available accelerator. Kaggle currently documents a weekly GPU quota of around 30 hours, sometimes higher depending on demand and resources.

Add these **Kaggle Notebook secrets** (Notebook → Add-ons → Secrets) using the credential values from their issuing platforms:
- `VORAX_HOME_READ_TOKEN` — a GitHub token with read access to the private VORAX Home repository
- `VORAX_HOME_WRITE_TOKEN` — a GitHub token with permission to read/update queue issues and create Child proposal pull requests
- `VORAX_KAGGLE_CHILD_API_KEY` — a newly generated random shared key for VORAX's child-to-runtime API authentication; this is **not** a Kaggle Account API Token
- `HF_VAULT_TOKEN` — a Hugging Face write token for the private Dataset `Musombi/revelacode-vorax-vault`

Optional Kaggle Notebook secrets:
- `VORAX_CHILD_PROPOSAL_TOKEN` (otherwise the Home write token is used)
- `HF_INFERENCE_TOKEN`
- `LEGEND_TEACHER_API_KEY`
- `LEGEND_TEACHER_MODEL`
- `LEGEND_TEACHER_BASE_URL`

Set repository variable `KAGGLE_KERNEL_SLUG` only if you want a non-default kernel name. Do not set Koyeb or HF Child URLs in Phase 1. Kaggle remains an on-demand notebook worker, not an always-on API; the account's quota and session availability must be read from Kaggle itself.

### Hugging Face — selected Phase 1 Vault, optional Space later

Account: [huggingface.co/Musombi](https://huggingface.co/Musombi). The selected Vault is the **private Dataset** `Musombi/revelacode-vorax-vault`. The Kaggle worker's first successful Vault sync creates it as private if it does not exist, then verifies that the existing repository is private before uploading anything. If it is public, sync fails closed.

Add `HF_VAULT_TOKEN` as a **Kaggle Notebook secret**, with write access to that Dataset. Do not put it in source code. Free Hugging Face accounts currently have 100 GB of private storage across Hub repositories; this is an account-wide limit, not 100 GB per Dataset. Check remaining storage before uploading large media.

The optional backup Docker Space is `Musombi/revelacode-vorax-child`. Its deployment is manual-only in `.github/workflows/deploy-hf-child.yml`; it requires `HF_SPACE_TOKEN` and a plan that permits creating/running Docker Spaces. Hugging Face currently states that personal Docker/Gradio compute Spaces require PRO; free personal accounts may instead host up to two Gradio ZeroGPU Spaces, subject to eligibility. The account's plan has not been verified, so do not trigger this workflow or purchase PRO as part of Phase 1.

The public profile currently lists `Musombi/mvi-ai-engine` and `Musombi/OmniMind` as existing Spaces. `MVI AI Engine` is an existing service, while `OmniMind` currently shows the Docker starter page without an application file. The public Dataset `Musombi/Omnset` currently shows as empty. These existing resources are not the VORAX Vault or VORAX Child and must not be overwritten or repurposed without explicit approval.

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

**GO / NO-GO remains BLOCKED.** This PR establishes deployment/configuration workflows, honest health checks, X queueing, the controller, data-only trust gates and structured Vault sync. It does not establish that the external services have been deployed or that Kaggle media generation works.

Home must still collect and verify:
1. A real Kaggle session report with confirmed NVIDIA GPU telemetry.
2. Successful sync to the private HF Dataset Vault, with at least one Home-merged V-Lang method and one independently verified real artifact, including SHA-256 and size.
3. Real locally generated media per modality: non-empty PNG, non-silent WAV and decodable MP4. A synthetic Vault smoke-test file does not count as GPU generation proof.
4. At least one Kaggle-origin V-Lang method independently validated and merged by Home.
5. At least 90% verified common-X coverage, the complete seven-day campaign, green CI/security checks, and separate live health proof for any optional HTTP Child that is enabled.

AWS is postponed. No `hf-model/` or separate runtime engine is introduced. RevelaAI remains untouched.
