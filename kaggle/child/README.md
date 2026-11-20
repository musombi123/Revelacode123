# VORAX Kaggle Learning Connector

This is the on-demand Kaggle **script kernel** for the current VORAX learning architecture. It connects to the separate Hugging Face Teacher and Runtime services, reads only Home's metadata index, discovers Hub metadata, and forwards one Teacher-signed proposal to Runtime when a task is supplied.

## Required Kaggle secrets

In the Kaggle notebook editor, open **Add-ons → Secrets** and add:

- `VORAX_HOME_READ_TOKEN` — read-only access to the private Home repository, used only to read `templates/library/index.json`.
- `VORAX_KAGGLE_CHILD_API_KEY` — the shared Child authentication key used to authenticate with Teacher and Runtime.

## Optional one-run task

- `VORAX_X` — optional task text for one learning pass. Leave it unset for the first connection check: the connector then checks Teacher/Runtime health, reads Home metadata and completes metadata discovery without requesting model inference.
- An explicit `--task` argument takes priority over the `VORAX_X` environment variable, which takes priority over the optional Kaggle secret.
- The connector never prints the task value in its report. Do not put passwords, API keys, confidential records or sensitive personal data in task text.

## Do not add these secrets to Kaggle

The connector does not need or request `VORAX_HOME_WRITE_TOKEN`, `HF_VAULT_TOKEN`, `HF_READ_TOKEN`, `HF_TOKEN`, `HF_SPACE_TOKEN`, or `VORAX_TEACHER_ATTESTATION_KEY`. Those remain on the trusted Runtime/Teacher side. Kaggle never receives Home write authority, Vault authority, or the Teacher signing key.

## Runtime contract

- Script kernel; internet enabled; GPU disabled; no attached model or dataset sources.
- No local model weights, Transformers/Diffusers, media generation, queue processing, direct Vault writes or Home method-body downloads.
- Free-provider-only inference remains the default. A blank task does not call the inference endpoint.
- A successful learning pass is not a Home approval: Runtime validates the proposal and stores it as pending; Home's trusted gate remains authoritative.

The report is written to `/kaggle/working/vorax-learning-report.json` in the Kaggle session.
