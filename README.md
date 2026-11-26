# VORAX — One Input X

VORAX is a **learning engine, not a model**. One Input X, one V-Lang, one shared runtime. It learns how to invoke and control capabilities through verified, testable V-Lang methods. It does not absorb model weights, train models, host checkpoints or certify itself.

## Current gate

**GO / NO-GO: BLOCKED.**

- RevelaAI remains untouched on Gemini. No integration until GO.
- AWS is postponed.
- Koyeb is paused.
- Kaggle is the on-demand Away Hunter. It discovers model, Space and Dataset metadata, reads Home's metadata-only index, asks a panel of remote Teachers, and sends one independently testable V-Lang proposal to Runtime. It does not clone Home, download method bodies or weights, process queues, run a local model, generate media or write to Vault.
- No fabricated media, GPU telemetry or artifact hashes are accepted.

## Runtime topology

| Resource | Type | Responsibility |
|---|---|---|
| GitHub Home | Private source repository | Authoritative V-Lang method store and metadata index; accepts reviewed knowledge proposals through pull requests |
| Kaggle | Learning Connector | Home metadata-index read, multi-model remote Teacher panel, testable V-Lang proposals |
| **Musombi/vorax-teacher-service** | Hugging Face Gradio/ZeroGPU Space | Metadata discovery and remote Inference Providers teacher responses |
| **Musombi/vorax-runtime-service** | Hugging Face Gradio/ZeroGPU Space | Shared VORAX runtime, Child authentication, trusted V-Lang verification, Vault pending writes and isolated Home pull requests |
| **Musombi/revelacode-vorax-vault** | Existing private Hugging Face Dataset | Pending/verified V-Lang, checksums, reports and real verified artifacts |

Kaggle is not a GPU image worker. The Teacher calls remote provider infrastructure; it never claims Kaggle T4 use. The Runtime does not load model weights. The two Spaces are public Gradio/ZeroGPU HTTP endpoints with Child-key authentication; the Vault remains a private Dataset. Runtime creates an isolated proposal branch and pull request for each verified method; it never updates Home main directly. Vault promotion waits until the merged main metadata index confirms the exact method digest. Required trusted CI and human review must pass before merge. No remote Teacher inference is performed during deployment.

## Hugging Face implementation

See **docs/vorax/huggingface-services.md** for service routes, secrets, deployment and verification:

- Teacher Space: **deploy/hf-teacher-gradio/**
- Runtime Space: **deploy/hf-runtime-gradio/**
- Primary Gradio/ZeroGPU deployment: **.github/workflows/deploy-vorax-gradio-spaces.yml**
- Local deployment fallback: **scripts/deploy_hf_zerogpu_children.py**
- Read-only Vault check: **scripts/verify_hf_vault.py**

The primary workflow is manually dispatched from **main**, requires the exact confirmation phrase **DEPLOY-VORAX-GRAPH-2026**, and requires a successful **verify** check on the exact commit being deployed. Its GitHub Actions secret **HF_SPACE_TOKEN** must authenticate as **Musombi**. It updates only the two existing public Gradio Spaces; it refuses to create or convert Spaces, alter visibility or hardware, or replace the existing private Dataset. It preserves existing Space secrets and does not run remote inference or start Kaggle.

The local script is an alternative operator-controlled route. It requires a clean, current **main** branch, runs the focused packaging tests and full test suite, and requires typed approval of the exact commit SHA. It configures the service secrets, verifies the existing private Dataset read-only, packages the Gradio services and runs Teacher metadata-discovery plus authenticated Runtime identity smoke tests. Do not run the local and GitHub Actions deployment paths concurrently.

The older tag-based **.github/workflows/deploy-hf-two-children.yml** is a Docker deployment path, not the current Gradio/ZeroGPU release path. Do not dispatch it for the current two Spaces. The legacy **.github/workflows/deploy-hf-child.yml** and **Musombi/revelacode-vorax-child** Space are also not part of the current architecture. Do not overwrite unrelated Spaces or create a replacement Vault.

A successful Space upload is not proof that VORAX has completed a learning cycle. A real multi-model Teacher run is performed only by an explicitly started learning session, and a learned capability remains pending until Runtime verification, an isolated Home pull request, trusted CI, human review, merge and exact-digest reconciliation all succeed.

## Trust and Vault policy

Children send proposals to the authenticated Runtime; they never receive a GitHub write token. Runtime accepts only Teacher-signed candidates, reruns schema validation and every declared test in the trusted V-Lang interpreter, checks duplicates against Home's metadata index, and writes the passing candidate to Vault pending. Runtime creates an isolated Home proposal branch containing the V-Lang method and matching metadata-only index, then opens a pull request against **main**. Home review and trusted CI are mandatory; Runtime never writes directly to the base branch. The candidate remains in Vault pending while the PR is open. After merge, Runtime re-reads main's metadata-only index and promotes the Vault-pending bytes only when ID, domain, task, triggers and canonical SHA-256 all match.

Only the Runtime Service can write a Runtime-verified pending method to the Vault or open a Home proposal pull request. Runtime accepts only Teacher responses signed with the shared private `VORAX_TEACHER_ATTESTATION_KEY`; this signing key and the GitHub Home write token remain server-side and are never given to Kaggle. Teacher and Kaggle do not write directly to Vault or GitHub Home. Home sends Children only **templates/library/index.json** metadata summaries; no Home V-Lang method bodies are pulled into Children. No weights, **hf-model/**, or model architecture **config.json** belong in Vault. PNG/WAV/MP4 are UNKNOWN until real remote outputs pass format verification and independent SHA-256 calculation.

## Local verification

~~~bash
python -m pip install -r requirements.txt
python scripts/verify_library.py
python -m unittest discover -s tests -v
python -m compileall -q vorax scripts tests
~~~

The Hugging Face deployment is not live until the two Space /health endpoints and their required service smoke tests return successfully. A green code PR alone is not proof of deployment. Only GitHub Home can authorize GO.
