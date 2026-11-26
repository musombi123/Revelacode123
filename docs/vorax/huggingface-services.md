# VORAX Hugging Face Children and Dataset Vault

**Last recorded deployment evidence (2026-11-21): both Spaces returned HTTP 200 and the existing private Vault was readable. That is historical evidence, not a current health check. Kaggle live proof, the end-to-end Home PR learning flow, media verification and coverage gates remain incomplete. GO / NO-GO remains BLOCKED. Re-run the primary deployment workflow's health and route checks before treating either Space as currently live.**

This document supersedes the older single-space **revelacode-vorax-child** backup arrangement. Do not dispatch the legacy **.github/workflows/deploy-hf-child.yml** for this design.

## Architecture

VORAX is a learning engine, not a model. It has one Input X, one V-Lang, and one shared runtime. A Child learns how to invoke and control a capability by proposing a bounded V-Lang method; it does not absorb weights, train models, or certify its own proposals.

| Resource | Hugging Face type | Responsibility | Write authority |
|---|---|---|---|
| **Musombi/vorax-teacher-service** | Gradio/ZeroGPU Space | Metadata-only discovery of Hub models, Spaces and Datasets; remote Inference Providers responses | No Vault or Home write access |
| **Musombi/vorax-runtime-service** | Gradio/ZeroGPU Space | Shared VORAX runtime, trusted V-Lang test execution, child authentication, isolated Home proposal branches and pull requests | The only service permitted to write verified candidates to Vault pending and open Home proposal pull requests |
| **Musombi/revelacode-vorax-vault** | Existing private Dataset | V-Lang indexes, pending methods, Home-merged methods, checksums, reports and verified artifacts | Runtime only; Home remains the source of truth |

The two Spaces are public HTTP services protected by the VORAX Child key on all learning, inference, execution and verification routes. Health and model metadata discovery are read-only. This avoids making Kaggle authenticate to a private Space separately. The Vault remains private.

Kaggle's connector reads only **VORAX_HOME_READ_TOKEN** and **VORAX_KAGGLE_CHILD_API_KEY**. It does not request or read **VORAX_HOME_WRITE_TOKEN** or **HF_VAULT_TOKEN**; those write credentials remain exclusive to Runtime.

Kaggle is the on-demand Away Hunter. It discovers model, Space and Dataset metadata, reads only Home's metadata index, asks a bounded panel of remote Teachers (proposal → independent review → synthesis), and submits one testable candidate to Runtime. Its source is changed to this learning-only design; live Kaggle validation remains a separate proof step. It must not download weights, run Diffusers, generate media locally, write directly to Vault, process a queue during connection tests, or certify itself. Koyeb is paused.

RevelaAI remains untouched on Gemini. AWS remains postponed.

## 1. Teacher Space

Repository: **Musombi/vorax-teacher-service**

Deployment context: **deploy/hf-teacher-gradio/**

Routes:

| Route | Behavior |
|---|---|
| **GET /health** | 200 with service=teacher, configuration booleans, weights_downloaded=false, local_model_loaded=false, kaggle_t4_used=false |
| **GET /teacher/discover?query=X&limit=N&source=all** | Metadata for Hub models, Spaces and Datasets plus the Hugging Face Inference Providers router catalogue: IDs, tags, descriptions, downloads, likes, pipeline/library/SDK data and live provider mappings |
| **POST /teacher/ask** | Authenticated remote chat completion through Hugging Face Inference Providers |

Example request fields: prompt and model_id, for example owner/model:provider. The ask route requires x-child-id and x-child-key. Kaggle can consult several discovered models in one learning pass. Each response and service log includes model ID and the provider confirmed by the Inference Provider response header; if the provider header is absent or differs from the pinned provider, Teacher rejects the response. Signed provenance records remote inference, kaggle_t4_used=false and weights_downloaded=false. There is no local model runtime or weight cache. The inference call is explicit; deployment health checks do not consume inference quota.

Teacher Space secrets:
- **HF_TOKEN** — Hugging Face token with Inference Providers permission.
- **HF_READ_TOKEN** — required Hub metadata-read token for model, Space and Dataset discovery.
- **VORAX_CHILD_ID=kaggle-child**.
- **VORAX_KAGGLE_CHILD_API_KEY**.
- **VORAX_TEACHER_ATTESTATION_KEY** — shared only with Runtime; never place it in Kaggle Secrets.
- **VORAX_ALLOW_BILLABLE_INFERENCE=false** — default is free-provider-only; Teacher refuses models without a live provider explicitly marked `is_free=true`. Setting this to `true` is an explicit billable-inference opt-in and may incur charges.

The Teacher has no Vault token and no GitHub Home write token.

## 2. Runtime Space

Repository: **Musombi/vorax-runtime-service**

Deployment context: **deploy/hf-runtime-gradio/**

This Space packages the repository's existing **vorax/** modules and trusted V-Lang interpreter. It does not package Home's **templates/library/** method bodies. Children receive only the metadata-only **templates/library/index.json** summaries and learn new implementations from the world.

Routes:

| Route | Behavior |
|---|---|
| **GET /health** | 200 with service=runtime, Home metadata-index state, private Dataset proof, secret readiness and ready |
| **GET /vorax/home-index** | Authenticated metadata-only Home index; method bodies are not included |
| **POST /vorax/x** | Answers only locally built-in runtime protocol X; otherwise returns unknown until a Child brings a verified method from the world |
| **POST /vorax/verify** | Revalidates a supplied Teacher-attested V-Lang method with the trusted interpreter, stores a passing method under Vault pending, then opens an isolated Home proposal pull request |
| **POST /vorax/learn** | Calls Teacher remotely, parses its proposed method, and passes it through the same Runtime verification and Home pull-request path |

**/vorax/x** accepts the documented JSON fields x, child_id and api_key. The same credentials may be sent in the x-child-id and x-child-key headers instead; headers are preferred for normal clients. Missing or invalid Child credentials are rejected. The request key is never written into the logs. The Runtime logs Child ID, redacted X, method ID, verification outcome and UTC timestamp.

A successful **hello, who are you** request should resolve through the shared **greeting.identity** V-Lang method and return **status=verified**. It is a method proof, not proof of media generation.

Candidate trust flow:
1. Teacher returns a proposed V-Lang object.
2. Runtime verifies the Teacher's HMAC-signed exact response, provider/model identity, remote-inference flags, timestamp and response hash; it also proves the submitted candidate is byte-for-byte equivalent as canonical JSON to the method inside that signed response. Runtime then overwrites provenance with the authenticated Child and Teacher identity.
3. Runtime reruns schema validation and every declared test using the repository's trusted interpreter.
4. Runtime writes only to **vlang/pending/** and updates **vlang/pending/index.json**, verification report and SHA-256 manifest.
5. Runtime creates an isolated proposal branch containing the V-Lang method file and updated metadata-only Home index, then opens a pull request against the configured Home base branch. It never updates the base branch. Kaggle never receives the Home write token.
6. The candidate stays in Vault pending while the pull request is open. Only after human review, required trusted CI checks and merge does Runtime re-read the base branch's metadata-only index and promote the Vault-pending bytes when the same ID, domain, task, triggers and exact canonical method SHA-256 are present. It never pulls the method implementation from Home. A failed PR creation or an unmerged PR leaves the method pending.

Runtime Space secrets:
- **VORAX_CHILD_ID=kaggle-child**.
- **VORAX_CHILD_ROLE=hf-runtime**.
- **VORAX_KAGGLE_CHILD_API_KEY**.
- **VORAX_HOME_REPO=musombi123/Revelacode123**, **VORAX_HOME_BRANCH=main**.
- **VORAX_HOME_READ_TOKEN** and **VORAX_HOME_WRITE_TOKEN**. The Runtime token needs repository Contents read/write and Pull requests read/write permissions to create isolated proposal branches and PRs; it must not bypass base-branch review or protection. Do not give it to Kaggle.
- **HF_VAULT_TOKEN** with read/write access to the existing private Dataset.
- **HF_INFERENCE_TOKEN** — remote provider access used by the Runtime's registered tool adapters; the deploy workflow currently maps this to the supplied `HF_TOKEN`.
- **HF_VAULT_REPO=Musombi/revelacode-vorax-vault**.
- **VORAX_TEACHER_URL=https://musombi-vorax-teacher-service.hf.space**.
- **VORAX_TEACHER_MODEL** — optional default used by /vorax/learn.
- **VORAX_TEACHER_ATTESTATION_KEY** — the same private HMAC key configured on Teacher; Kaggle must never receive it.
- **VORAX_ALLOW_BILLABLE_INFERENCE=false** — also controls Runtime's remote artifact-tool adapters; they make no provider calls unless explicitly opted in.

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

Teacher and Kaggle select only live providers whose router metadata explicitly reports `is_free=true` by default. The Runtime's remote artifact-tool adapters are also disabled by default. The optional service setting `VORAX_ALLOW_BILLABLE_INFERENCE=true` explicitly opts those paths into potentially billable provider calls. The deployment script writes this setting as `false` unless the operator explicitly supplies another value. A provider that omits the free-status field is not treated as free.

The Runtime writes a pending method only after trusted V-Lang tests pass. Teacher and Kaggle have no direct Vault write path. When Home's metadata-only index confirms the merged method ID, Runtime rechecks that the method already in Vault pending has the same domain, task and triggers, matches the SHA-256 published in Home's metadata index, and passes the V-Lang tests again, then promotes that Vault-stored method to **vlang/verified/**. Runtime never downloads a Home method body. Media enters the Vault only after real format verification and SHA-256 calculation. Until a real PNG/WAV/MP4 is independently verified, its status remains UNKNOWN.

## 4. Deployment

The Teacher and Runtime use separate **Gradio Server-mode / ZeroGPU** packages. The primary production path is **.github/workflows/deploy-vorax-gradio-spaces.yml**:
- manually dispatched from **main** with the exact confirmation phrase `DEPLOY-VORAX-GRAPH-2026`;
- waits for a successful **verify** check on the exact commit SHA being deployed;
- requires the GitHub Actions secret **HF_SPACE_TOKEN**, authenticated as the existing Space owner **Musombi**;
- updates only the two existing public Gradio Spaces and refuses to create/convert Spaces, change visibility or change hardware;
- preserves the Space secrets already configured in Hugging Face;
- waits for Teacher health, verifies free-provider-only policy, waits for Runtime `ready=true`, verifies the metadata-only boundary and checks every required Runtime OpenAPI route/method.

The primary workflow does not set or rotate service secrets, run remote Teacher inference, or start Kaggle. The following Space secrets must already be configured. Teacher: **HF_TOKEN**, **HF_READ_TOKEN**, **VORAX_CHILD_ID**, **VORAX_KAGGLE_CHILD_API_KEY**, and **VORAX_TEACHER_ATTESTATION_KEY**, with **VORAX_ALLOW_BILLABLE_INFERENCE=false**. Runtime: **VORAX_CHILD_ID**, **VORAX_CHILD_ROLE=hf-runtime**, **VORAX_KAGGLE_CHILD_API_KEY**, **VORAX_HOME_REPO**, **VORAX_HOME_BRANCH**, **VORAX_HOME_READ_TOKEN**, **VORAX_HOME_WRITE_TOKEN**, **HF_VAULT_TOKEN**, **HF_VAULT_REPO**, **HF_INFERENCE_TOKEN**, **VORAX_TEACHER_ATTESTATION_KEY**, and **VORAX_TEACHER_URL**, with billable inference disabled.

The local operator fallback **scripts/deploy_hf_zerogpu_children.py** requires a clean local **main** equal to **origin/main**, runs the focused packaging tests and the full test suite, and requires typed approval of the exact commit SHA. It uses the required credentials **HF_SPACE_TOKEN**, **HF_TOKEN**, **HF_READ_TOKEN**, **VORAX_KAGGLE_CHILD_API_KEY**, **VORAX_HOME_READ_TOKEN**, **VORAX_HOME_WRITE_TOKEN**, **HF_VAULT_TOKEN**, and **VORAX_TEACHER_ATTESTATION_KEY**. It performs a read-only audit of the existing private Vault, configures Space secrets, uploads the two packages, checks Teacher metadata discovery and performs an authenticated Runtime identity smoke test without billable inference. Never paste credentials into chat or commit them. Do not run this fallback concurrently with the GitHub Actions release.

The older tag-based **.github/workflows/deploy-hf-two-children.yml** deploys the legacy Docker packages. It is not the current Gradio/ZeroGPU deployment path and must not be dispatched for these two Spaces. The Hugging Face Space write token is used only by the deployment process; the Runtime's GitHub Home token stays server-side and needs Contents read/write and Pull requests read/write on Home. The Teacher attestation key is shared only between Teacher and Runtime; Kaggle never receives it.

No model weights are uploaded. Billable inference remains disabled. A real Teacher inference request is a separate learning operation and is not part of deployment health checks. The primary workflow never creates, converts, deletes or replaces the existing private Dataset. The local fallback may create a missing Space only after explicit operator approval, but it never creates or replaces the Vault.

## GO / NO-GO

**BLOCKED.** A successful code PR or Space upload does not authorize integration or GO. Still required:
- Kaggle Learning Connector separately validated with metadata-only discovery and zero weight download/Diffusers/media/queue/Vault writes during connection test.
- Teacher and Runtime both live and their required endpoints verified.
- Existing private Dataset verified intact, with at least one Runtime-verified pending V-Lang test record and checksum.
- One actual Kaggle-origin learned method goes through Teacher → Runtime → Vault pending → isolated Home proposal PR → real CI green → human-reviewed merge → exact metadata/SHA reconciliation → Runtime promotion.
- Real PNG, WAV and MP4 from approved remote adapters each independently format-verified and SHA-256 recorded; otherwise each remains UNKNOWN.
- At least 90% verified common-X coverage and a completed seven-day campaign.

Home's `main` branch and metadata index remain the knowledge authority. The authenticated Runtime may create isolated knowledge-proposal branches and PRs only after signed Teacher provenance, trusted V-Lang tests and duplicate checks. It must never update Home `main` directly. Code, dependency, secret, deployment-workflow and permission changes still require operator review. GO stays BLOCKED until every required proof exists.
