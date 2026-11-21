# VORAX Hugging Face Children and Dataset Vault

**Status: implementation branch prepared; both Spaces are not yet deployed. GO / NO-GO remains BLOCKED.**

This document supersedes the older single-space **revelacode-vorax-child** backup arrangement. Do not dispatch the legacy **.github/workflows/deploy-hf-child.yml** for this design.

## Architecture

VORAX is a learning engine, not a model. It has one Input X, one V-Lang, and one shared runtime. A Child learns how to invoke and control a capability by proposing a bounded V-Lang method; it does not absorb weights, train models, or certify its own proposals.

| Resource | Hugging Face type | Responsibility | Write authority |
|---|---|---|---|
| **Musombi/vorax-teacher-service** | Gradio/ZeroGPU Space | Metadata-only discovery of Hub models, Spaces and Datasets; remote Inference Providers responses | No Vault or Home write access |
| **Musombi/vorax-runtime-service** | Gradio/ZeroGPU Space | Shared VORAX runtime, trusted V-Lang test execution, child authentication, atomic direct commits to Home | The only service permitted to write verified candidates to Vault pending and Home |
| **Musombi/revelacode-vorax-vault** | Existing private Dataset | V-Lang indexes, pending methods, Home-merged methods, checksums, reports and verified artifacts | Runtime only; Home remains the source of truth |

The two Spaces are public HTTP services protected by the VORAX Child key on all learning, inference, execution and verification routes. Health and model metadata discovery are read-only. This avoids making Kaggle authenticate to a private Space separately. The Vault remains private.

Kaggle's connector reads only **VORAX_HOME_READ_TOKEN** and **VORAX_KAGGLE_CHILD_API_KEY**. It does not request or read **VORAX_HOME_WRITE_TOKEN** or **HF_VAULT_TOKEN**; those write credentials remain exclusive to Runtime.

Kaggle is the on-demand Away Hunter. It discovers model, Space and Dataset metadata, reads only Home's metadata index, asks a bounded panel of remote Teachers (proposal → independent review → synthesis), and submits one testable candidate to Runtime. Its source is changed to this learning-only design; live Kaggle validation remains a separate proof step. It must not download weights, run Diffusers, generate media locally, write directly to Vault, process a queue during connection tests, or certify itself. Koyeb is paused.

RevelaAI remains untouched on Gemini. AWS remains postponed.

## 1. Teacher Space

Repository: **Musombi/vorax-teacher-service**

Deployment context: **deploy/hf-teacher/**

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

Deployment context: **deploy/hf-runtime/**

This Space packages the repository's existing **vorax/** modules and trusted V-Lang interpreter. It does not package Home's **templates/library/** method bodies. Children receive only the metadata-only **templates/library/index.json** summaries and learn new implementations from the world.

Routes:

| Route | Behavior |
|---|---|
| **GET /health** | 200 with service=runtime, Home metadata-index state, private Dataset proof, secret readiness and ready |
| **GET /vorax/home-index** | Authenticated metadata-only Home index; method bodies are not included |
| **POST /vorax/x** | Answers only locally built-in runtime protocol X; otherwise returns unknown until a Child brings a verified method from the world |
| **POST /vorax/verify** | Revalidates a supplied V-Lang method with the trusted interpreter, stores a passing method under Vault pending, then atomically commits the method and metadata index to Home main |
| **POST /vorax/learn** | Calls Teacher remotely, parses its proposed method, and passes it through the same Runtime verification and direct Home commit path |

**/vorax/x** accepts the documented JSON fields x, child_id and api_key. The same credentials may be sent in the x-child-id and x-child-key headers instead; headers are preferred for normal clients. Missing or invalid Child credentials are rejected. The request key is never written into the logs. The Runtime logs Child ID, redacted X, method ID, verification outcome and UTC timestamp.

A successful **hello, who are you** request should resolve through the shared **greeting.identity** V-Lang method and return **status=verified**. It is a method proof, not proof of media generation.

Candidate trust flow:
1. Teacher returns a proposed V-Lang object.
2. Runtime verifies the Teacher's HMAC-signed exact response, provider/model identity, remote-inference flags, timestamp and response hash; it also proves the submitted candidate is byte-for-byte equivalent as canonical JSON to the method inside that signed response. Runtime then overwrites provenance with the authenticated Child and Teacher identity.
3. Runtime reruns schema validation and every declared test using the repository's trusted interpreter.
4. Runtime writes only to **vlang/pending/** and updates **vlang/pending/index.json**, verification report and SHA-256 manifest.
5. Runtime atomically writes the V-Lang method file and updated metadata-only Home index in one Git commit to the configured Home branch. The Git ref update is non-forced; a concurrent Home update is never overwritten. Kaggle never receives the Home write token.
6. Runtime re-reads Home's metadata-only index and promotes the method bytes already in Vault pending to **vlang/verified/** only when the same ID, domain, task, triggers and exact canonical method SHA-256 are present. It never pulls the method implementation from Home. A direct-commit failure leaves the method pending and does not self-promote it.

Runtime Space secrets:
- **VORAX_CHILD_ID=kaggle-child**.
- **VORAX_CHILD_ROLE=hf-runtime**.
- **VORAX_KAGGLE_CHILD_API_KEY**.
- **VORAX_HOME_REPO=musombi123/Revelacode123**, **VORAX_HOME_BRANCH=main**.
- **VORAX_HOME_READ_TOKEN** and **VORAX_HOME_WRITE_TOKEN**. The Runtime token needs repository Contents read/write permission and direct update permission on the configured Home branch; do not give it to Kaggle.
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

The Teacher and Runtime use separate **Gradio Server-mode / ZeroGPU** packages. The deployment script **scripts/deploy_hf_zerogpu_children.py**:
- runs the focused ZeroGPU packaging tests and the full local test suite;
- requires explicit typed approval for the exact clean `main` commit;
- checks the existing private Vault read-only before changing either Space;
- creates or verifies exactly two public Gradio Spaces with ZeroGPU hardware and no CPU Basic/paid fallback;
- configures Space secrets without printing their values;
- uploads the Teacher and Runtime Gradio packages, excluding model weights and local model loaders;
- waits for `/health`, checks Teacher metadata discovery, and performs the authenticated Runtime identity smoke test without paid inference.

Local deployment runs on Windows PowerShell or another supported Python environment. The process requires **HF_SPACE_TOKEN**, **HF_TOKEN**, **HF_READ_TOKEN**, **VORAX_KAGGLE_CHILD_API_KEY**, **VORAX_HOME_READ_TOKEN**, **VORAX_HOME_WRITE_TOKEN**, **HF_VAULT_TOKEN**, and **VORAX_TEACHER_ATTESTATION_KEY**. Never paste these values into chat or commit them. GitHub Actions are not needed for child knowledge submissions; a local deployment approval does not claim that GitHub Actions passed.

The Hugging Face Space write token is used only to provision the two Spaces and set their secrets. The Runtime's GitHub Home token is stored as a Space secret and needs repository Contents read/write access on Home. The Teacher attestation key and child key are shared only with the services that require them; Kaggle receives only its child API key.

No model weights are uploaded. Billable inference remains disabled. A real Teacher inference request is a separate learning operation and is not part of deployment health checks. The existing private Dataset is never created, converted, deleted or replaced. If the account cannot create free ZeroGPU Spaces, deployment stops without selecting paid hardware.

## GO / NO-GO

**BLOCKED.** A successful code PR or Space upload does not authorize integration or GO. Still required:
- Kaggle Learning Connector separately validated with metadata-only discovery and zero weight download/Diffusers/media/queue/Vault writes during connection test.
- Teacher and Runtime both live and their required endpoints verified.
- Existing private Dataset verified intact, with at least one Runtime-verified pending V-Lang test record and checksum.
- One actual Kaggle-origin learned method goes through Teacher → Runtime → Vault pending → atomic direct Home commit → exact metadata/SHA reconciliation → Runtime promotion.
- Real PNG, WAV and MP4 from approved remote adapters each independently format-verified and SHA-256 recorded; otherwise each remains UNKNOWN.
- At least 90% verified common-X coverage and a completed seven-day campaign.

Home's `main` branch and metadata index remain the knowledge authority. Direct knowledge commits are permitted only from the authenticated Runtime after signed Teacher provenance, trusted V-Lang tests and duplicate checks; code, dependency, secret, deployment-workflow and permission changes still require operator review. GO stays BLOCKED until every required proof exists.
