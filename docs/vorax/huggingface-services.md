# VORAX Hugging Face Children and Dataset Vault

**Status: implementation branch prepared; both Spaces are not yet deployed. GO / NO-GO remains BLOCKED.**

This document supersedes the older single-space **revelacode-vorax-child** backup arrangement. Do not dispatch the legacy **.github/workflows/deploy-hf-child.yml** for this design.

## Architecture

VORAX is a learning engine, not a model. It has one Input X, one V-Lang, and one shared runtime. A Child learns how to invoke and control a capability by proposing a bounded V-Lang method; it does not absorb weights, train models, or certify its own proposals.

| Resource | Hugging Face type | Responsibility | Write authority |
|---|---|---|---|
| **Musombi/vorax-teacher-service** | Docker Space | Metadata-only discovery of Hub models, Spaces and Datasets; remote Inference Providers responses | No Vault write access |
| **Musombi/vorax-runtime-service** | Docker Space | Shared VORAX runtime, trusted V-Lang test execution, child authentication, Home PR submission | The only service permitted to write verified candidates to Vault pending |
| **Musombi/revelacode-vorax-vault** | Existing private Dataset | V-Lang indexes, pending methods, Home-merged methods, checksums, reports and verified artifacts | Runtime only; Home remains the source of truth |

The two Spaces are public HTTP services protected by the VORAX Child key on all learning, inference, execution and verification routes. Health and model metadata discovery are read-only. This avoids making Kaggle authenticate to a private Space separately. The Vault remains private.

Kaggle is the on-demand Away Hunter. It discovers model, Space and Dataset metadata, reads only Home's metadata index, asks a bounded panel of remote Teachers (proposal → independent review → synthesis), and submits one testable candidate to Runtime. Its source is changed to this learning-only design; live Kaggle validation remains a separate proof step. It must not download weights, run Diffusers, generate media locally, write directly to Vault, process a queue during connection tests, or certify itself. Koyeb is paused.

RevelaAI remains untouched on Gemini. AWS remains postponed.

## 1. Teacher Space

Repository: **Musombi/vorax-teacher-service**

Deployment context: **deploy/hf-teacher/**

Routes:

| Route | Behavior |
|---|---|
| **GET /health** | 200 with service=teacher, configuration booleans, weights_downloaded=false, local_model_loaded=false, kaggle_t4_used=false |
| **GET /teacher/discover?query=X&limit=N&source=all** | Metadata for Hub models, Spaces and Datasets: id, tags, description, downloads, likes, pipeline/library/SDK metadata |
| **POST /teacher/ask** | Authenticated remote chat completion through Hugging Face Inference Providers |

Example request fields: prompt and model_id, for example owner/model:provider. The ask route requires x-child-id and x-child-key. Kaggle can consult several discovered models in one learning pass. Each response and service log includes model ID, provider (or unknown if the provider header is absent), remote inference status, and kaggle_t4_used=false. There is no local model runtime or weight cache. The inference call is explicit; deployment health checks do not consume inference quota.

Teacher Space secrets:
- **HF_TOKEN** — Hugging Face token with Inference Providers permission.
- **HF_READ_TOKEN** — optional token for Hub metadata discovery.
- **VORAX_CHILD_ID=kaggle-child**.
- **VORAX_KAGGLE_CHILD_API_KEY**.
- **VORAX_TEACHER_ATTESTATION_KEY** — shared only with Runtime; never place it in Kaggle Secrets.

The Teacher has no Vault token and no GitHub Home write token.

## 2. Runtime Space

Repository: **Musombi/vorax-runtime-service**

Deployment context: **deploy/hf-runtime/**

This Space packages the repository's existing **vorax/** modules and trusted V-Lang interpreter. It does not package Home's **templates/library/** method bodies. Children receive only the metadata-only **templates/library/index.json** summaries and learn new implementations from the world.

Routes:

| Route | Behavior |
|---|---|
| **GET /health** | 200 with service=runtime, Home metadata-index state, private Dataset proof, secret readiness and ready |
| **GET /vorax/home-index** | Authenticated metadata-only Home index; method bodies are not included |
| **POST /vorax/x** | Answers only locally built-in runtime protocol X; otherwise returns unknown until a Child brings a verified method from the world |
| **POST /vorax/verify** | Revalidates a supplied V-Lang method with the trusted interpreter, stores a passing method under Vault pending, then opens a Home PR |
| **POST /vorax/learn** | Calls Teacher remotely, parses its proposed method, and passes it through the same Runtime verification and Home PR path |

**/vorax/x** accepts the documented JSON fields x, child_id and api_key. The same credentials may be sent in the x-child-id and x-child-key headers instead; headers are preferred for normal clients. Missing or invalid Child credentials are rejected. The request key is never written into the logs. The Runtime logs Child ID, redacted X, method ID, verification outcome and UTC timestamp.

A successful **hello, who are you** request should resolve through the shared **greeting.identity** V-Lang method and return **status=verified**. It is a method proof, not proof of media generation.

Candidate trust flow:
1. Teacher returns a proposed V-Lang object.
2. Runtime verifies the Teacher's HMAC-signed exact response, provider/model identity, remote-inference flags, timestamp and response hash; it also proves the submitted candidate is byte-for-byte equivalent as canonical JSON to the method inside that signed response. Runtime then overwrites provenance with the authenticated Child and Teacher identity.
3. Runtime reruns schema validation and every declared test using the repository's trusted interpreter.
4. Runtime writes only to **vlang/pending/** and updates **vlang/pending/index.json**, verification report and SHA-256 manifest.
5. Runtime opens a GitHub Home PR. The pending method is deliberately **not** inserted into the executable Runtime catalogue.
6. After Home merges the PR, Runtime may promote the method bytes already in Vault pending to **vlang/verified/** when the refreshed Home metadata index includes its ID. It never pulls the method implementation from Home.

Runtime Space secrets:
- **VORAX_CHILD_ID=kaggle-child**.
- **VORAX_CHILD_ROLE=hf-runtime**.
- **VORAX_KAGGLE_CHILD_API_KEY**.
- **VORAX_HOME_REPO=musombi123/Revelacode123**, **VORAX_HOME_BRANCH=main**.
- **VORAX_HOME_READ_TOKEN** and **VORAX_HOME_WRITE_TOKEN**.
- **HF_VAULT_TOKEN** with read/write access to the existing private Dataset.
- **HF_VAULT_REPO=Musombi/revelacode-vorax-vault**.
- **VORAX_TEACHER_URL=https://musombi-vorax-teacher-service.hf.space**.
- **VORAX_TEACHER_MODEL** — optional default used by /vorax/learn.
- **VORAX_TEACHER_ATTESTATION_KEY** — the same private HMAC key configured on Teacher; Kaggle must never receive it.

## 3. Preserve the existing Vault

The deployment and verification scripts never create, delete, convert, or replace a Dataset. **scripts/verify_hf_vault.py** is read-only. The Runtime's Vault client uses repo_type=dataset, verifies privacy and refuses to continue if it sees hf-model/, weight files, or a config.json declaring architectures or model_type.

Required existing index/manifest/report files:
- **vlang/index.json**
- **vlang/pending/index.json**
- **manifest/checksums.json**
- **manifest/runtime-version.json**
- **manifest/sync-status.json**
- **reports/devour/latest.json**
- **reports/verification/latest.json**
- **reports/go-no-go/latest.json**

Artifact prefixes remain **artifacts/images/**, **artifacts/audio/**, and **artifacts/video/**. Empty directories may not appear in Hub listings until a file is uploaded. No model weights, model configs or model training checkpoints belong in this Dataset.

The Runtime writes a pending method only after trusted V-Lang tests pass. Teacher and Kaggle have no direct Vault write path. When Home's metadata-only index confirms the merged method ID, Runtime rechecks that the method already in Vault pending has the same domain, task and triggers, revalidates its V-Lang tests and SHA-256, then promotes that Vault-stored method to **vlang/verified/**. Runtime never downloads a Home method body. Media enters the Vault only after real format verification and SHA-256 calculation. Until a real PNG/WAV/MP4 is independently verified, its status remains UNKNOWN.

## 4. Deployment

The two Docker Space build contexts are independent. The deployment script:
- checks the existing Vault read-only first;
- creates/opens the exact two Space repositories as Docker Spaces;
- sets the Space secrets without printing values;
- uploads only the Teacher or Runtime context;
- excludes the Kaggle local-GPU provider file from the Runtime package;
- never requests paid Space hardware;
- waits for /health, checks metadata discovery, and sends the Runtime greeting X.

Local deployment commands:
- python -m pip install -r requirements.txt
- python scripts/verify_hf_vault.py
- python scripts/deploy_hf_children.py --health-wait-seconds 300

The script requires **HF_SPACE_TOKEN**, **HF_TOKEN**, **VORAX_KAGGLE_CHILD_API_KEY**, **VORAX_HOME_READ_TOKEN**, **VORAX_HOME_WRITE_TOKEN**, and **HF_VAULT_TOKEN** in the local process environment. **HF_READ_TOKEN** and **VORAX_TEACHER_MODEL** are optional. Never paste these values into chat or commit them.

Optional, deliberately explicit smoke action: **--smoke-remote-inference --teacher-model owner/model:provider** makes one actual remote inference request and may consume provider quota/credits. Deployment does not fabricate or seed a V-Lang method.

The equivalent release-gated GitHub Actions workflow is **.github/workflows/deploy-hf-two-children.yml**. It runs only from a published tag that passes **scripts/require_green_release.py**. Required GitHub Secrets are **HF_SPACE_TOKEN**, **HF_TOKEN**, **VORAX_KAGGLE_CHILD_API_KEY**, **VORAX_HOME_READ_TOKEN**, **VORAX_HOME_WRITE_TOKEN**, **HF_VAULT_TOKEN**, and **VORAX_TEACHER_ATTESTATION_KEY**; optional **HF_READ_TOKEN** and repository variable **VORAX_TEACHER_MODEL** may also be set.

**Hosting constraint:** Hugging Face documents Docker Spaces as compute Spaces. Personal-account CPU Basic for Docker/Gradio currently requires PRO eligibility; free Static Spaces cannot host this FastAPI Docker service. The script does not upgrade a plan, add billing, or select paid hardware. If Space creation is rejected by account eligibility, deployment stops and reports the service as not live.

## GO / NO-GO

**BLOCKED.** A successful code PR or Space upload does not authorize integration or GO. Still required:
- Kaggle Learning Connector separately validated with metadata-only discovery and zero weight download/Diffusers/media/queue/Vault writes during connection test.
- Teacher and Runtime both live and their required endpoints verified.
- Existing private Dataset verified intact, with at least one Runtime-verified pending V-Lang test record and checksum.
- One actual Kaggle-origin learned method goes through Teacher → Runtime → Vault pending → GitHub Home PR → green exact-head CI → trusted merge.
- Real PNG, WAV and MP4 from approved remote adapters each independently format-verified and SHA-256 recorded; otherwise each remains UNKNOWN.
- At least 90% verified common-X coverage and a completed seven-day campaign.

Home alone controls review and merge. No code, dependencies, secrets, deployment workflows or privileged permission changes may be auto-merged. GO stays BLOCKED until every required proof exists.
