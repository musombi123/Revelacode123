# VORAX — One Input X

VORAX is a **learning engine, not a model**. One Input X, one V-Lang, one shared runtime. It learns how to invoke and control capabilities through verified, testable V-Lang methods. It does not absorb model weights, train models, host checkpoints or certify itself.

## Current gate

**GO / NO-GO: BLOCKED.**

- RevelaAI remains untouched on Gemini. No integration until GO.
- AWS is postponed.
- Koyeb is paused.
- Kaggle is a metadata-only Learning Connector. It must not download weights, run Diffusers, generate local media, process queues or write to Vault during connection tests. Its separate connector validation is not part of the Hugging Face deployment task.
- No fabricated media, GPU telemetry or artifact hashes are accepted.

## Runtime topology

| Resource | Type | Responsibility |
|---|---|---|
| GitHub Home | Private source repository | Trusted V-Lang validation, CI, PR review, merge and release authority |
| Kaggle | Learning Connector | Hub metadata discovery, remote Teacher calls, testable method proposals |
| **Musombi/vorax-teacher-service** | Hugging Face Docker Space | Metadata discovery and remote Inference Providers teacher responses |
| **Musombi/vorax-runtime-service** | Hugging Face Docker Space | Shared VORAX runtime, Child authentication, trusted method verification, Vault pending writes and Home PRs |
| **Musombi/revelacode-vorax-vault** | Existing private Hugging Face Dataset | Pending/verified V-Lang, checksums, reports and real verified artifacts |

Kaggle is not a GPU image worker. The Teacher calls remote provider infrastructure; it never claims Kaggle T4 use. The Runtime does not load model weights. The two Spaces are public HTTP endpoints with Child-key authentication; the Vault remains a private Dataset.

## Hugging Face implementation

See **docs/vorax/huggingface-services.md** for service routes, secrets, deployment and verification:

- Teacher Space: **deploy/hf-teacher/**
- Runtime Space: **deploy/hf-runtime/**
- Deployment: **scripts/deploy_hf_children.py**
- Read-only Vault check: **scripts/verify_hf_vault.py**
- Release-gated deploy workflow: **.github/workflows/deploy-hf-two-children.yml**

The legacy **.github/workflows/deploy-hf-child.yml** is not the deployment path for the two-service architecture. Do not reuse the old **Musombi/revelacode-vorax-child** Space or overwrite unrelated Spaces.

The deployment script never creates or converts a Dataset. It verifies the existing **Musombi/revelacode-vorax-vault** as a private Dataset, checks for model weights/architecture configs, uploads the two Docker Space contexts, and tests health/metadata/Runtime greeting routes. Real inference and a synthetic pending-method PR are opt-in because they have additional effects.

## Trust and Vault policy

Children submit methods as GitHub pull requests. Runtime schema validation and V-Lang tests are necessary but do not replace Home review. Pending candidates remain in **vlang/pending/**; they do not enter the executable catalogue before Home merges them. Home-merged methods may be mirrored to **vlang/verified/**.

Only the Runtime Service can write a Runtime-verified pending method to the Vault. Teacher and Kaggle do not write directly to Vault. No weights, **hf-model/**, or model architecture **config.json** belong in Vault. PNG/WAV/MP4 are UNKNOWN until real remote outputs pass format verification and independent SHA-256 calculation.

## Local verification

~~~bash
python -m pip install -r requirements.txt
python scripts/verify_library.py
python -m unittest discover -s tests -v
python -m compileall -q vorax scripts tests
~~~

The Hugging Face deployment is not live until the two Space /health endpoints and their required service smoke tests return successfully. A green code PR alone is not proof of deployment. Only GitHub Home can authorize GO.
