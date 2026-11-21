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
| GitHub Home | Private source repository | Authoritative V-Lang method store and metadata index; accepts atomic commits only from the verified Runtime |
| Kaggle | Learning Connector | Home metadata-index read, multi-model remote Teacher panel, testable V-Lang proposals |
| **Musombi/vorax-teacher-service** | Hugging Face Gradio/ZeroGPU Space | Metadata discovery and remote Inference Providers teacher responses |
| **Musombi/vorax-runtime-service** | Hugging Face Gradio/ZeroGPU Space | Shared VORAX runtime, Child authentication, trusted V-Lang verification, Vault pending writes and atomic direct commits to Home |
| **Musombi/revelacode-vorax-vault** | Existing private Hugging Face Dataset | Pending/verified V-Lang, checksums, reports and real verified artifacts |

Kaggle is not a GPU image worker. The Teacher calls remote provider infrastructure; it never claims Kaggle T4 use. The Runtime does not load model weights. The two Spaces are public Gradio/ZeroGPU HTTP endpoints with Child-key authentication; the Vault remains a private Dataset. The learning write path does not use GitHub Actions: Runtime writes verified methods through the GitHub API after local service-side validation. No remote Teacher inference is performed during deployment.

## Hugging Face implementation

See **docs/vorax/huggingface-services.md** for service routes, secrets, deployment and verification:

- Teacher Space: **deploy/hf-teacher-gradio/**
- Runtime Space: **deploy/hf-runtime-gradio/**
- Deployment: **scripts/deploy_hf_zerogpu_children.py**
- Read-only Vault check: **scripts/verify_hf_vault.py**
- Release-gated deploy workflow: **.github/workflows/deploy-hf-two-children.yml**

The legacy **.github/workflows/deploy-hf-child.yml** is not the deployment path for the two-service architecture. Do not reuse the old **Musombi/revelacode-vorax-child** Space or overwrite unrelated Spaces.

The deployment script never creates or converts a Dataset. It verifies the existing **Musombi/revelacode-vorax-vault** as a private Dataset, checks for model weights/architecture configs, uploads the two Gradio/ZeroGPU Space contexts, and tests health/metadata/Runtime identity routes. A real multi-model teacher run is performed only by an explicitly started Kaggle learning session. Manual local deployment approval is supported; GitHub Actions are not required.

## Trust and Vault policy

Children send proposals to the authenticated Runtime; they never receive a GitHub write token. Runtime accepts only Teacher-signed candidates, reruns schema validation and every declared test in the trusted V-Lang interpreter, checks duplicates against Home's metadata index, and writes the passing candidate to Vault pending. Runtime then atomically commits the V-Lang method and updated metadata-only index to Home's **main** branch in one Git commit. This deliberately replaces the knowledge-submission PR/review step; it does not allow arbitrary Python or shell code. The Home commit is authoritative. Runtime re-reads the metadata-only index and promotes the Vault-pending bytes only when ID, domain, task, triggers and canonical SHA-256 all match.

Only the Runtime Service can write a Runtime-verified pending method to the Vault or commit an accepted method to Home. Runtime accepts only Teacher responses signed with the shared private `VORAX_TEACHER_ATTESTATION_KEY`; this signing key and the GitHub Home write token remain server-side and are never given to Kaggle. Teacher and Kaggle do not write directly to Vault or GitHub Home. Home sends Children only **templates/library/index.json** metadata summaries; no Home V-Lang method bodies are pulled into Children. No weights, **hf-model/**, or model architecture **config.json** belong in Vault. PNG/WAV/MP4 are UNKNOWN until real remote outputs pass format verification and independent SHA-256 calculation.

## Local verification

~~~bash
python -m pip install -r requirements.txt
python scripts/verify_library.py
python -m unittest discover -s tests -v
python -m compileall -q vorax scripts tests
~~~

The Hugging Face deployment is not live until the two Space /health endpoints and their required service smoke tests return successfully. A green code PR alone is not proof of deployment. Only GitHub Home can authorize GO.
