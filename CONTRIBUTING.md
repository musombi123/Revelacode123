# Contributing to VORAX — One Input X

VORAX is a learning engine, not a model. Contributions must preserve its model-free V-Lang method-learning architecture and its trust boundaries.

## Before proposing changes

1. Read README.md, VLANG_V1.md, and the relevant documents under docs/vorax/.
2. Check current issues, pull requests, and the current GO/NO-GO gate.
3. Propose architectural changes before implementation.
4. Do not connect RevelaAI, introduce model weights, or change the deployment topology without an explicit approved decision.

## Knowledge proposals versus code changes

- A V-Lang knowledge proposal is not a code change. It must be independently testable, pass the trusted Runtime verification path, and follow the existing isolated pull-request and human-review process.
- Code, CI, deployment, security, or infrastructure changes must be submitted as pull requests and pass required checks.
- Children must not write directly to Home or Vault. Do not bypass Runtime verification, metadata-index checks, digest validation, or human review.
- Never change Home's protected main branch directly.

## Required engineering rules

- Preserve one Input X, one V-Lang, and one shared Runtime.
- Do not add model weights, model directories, model architecture configuration, or model-training pipelines.
- Never commit tokens, private keys, Child keys, Vault credentials, or deployment secrets.
- Keep the Vault private and pending/verified promotion rules intact.
- Do not fabricate teacher attestations, artifact hashes, GPU telemetry, or test evidence.
- Keep Teacher, Runtime, Kaggle, Home, and Vault responsibilities separated.
- Add tests for parser, interpreter, verification, trust, and promotion changes.
- Update architecture and deployment documentation when contracts change.

## Pull request checklist

- [ ] The change is consistent with the current GO/NO-GO gate.
- [ ] Relevant tests and trusted CI checks pass.
- [ ] Trust boundaries and security implications are documented.
- [ ] No weights, secrets, or private Vault data are included.
- [ ] Documentation reflects the actual implementation.
- [ ] No claim of deployment or learning success is made without runtime evidence.

All code and infrastructure changes require maintainer review. A proposal is not accepted until the required checks and review complete.
