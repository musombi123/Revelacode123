# VORAX research library

**Project:** VORAX  
**Repository:** `musombi123/Revelacode123`  
**Evidence snapshot:** 25 November 2026  
**Purpose:** Founder study, technical research, architecture review and partner due diligence.

## Core definition

VORAX is a **verified capability-learning and invocation system, not a foundation model**. Its architecture centers on one Input X, one constrained V-Lang method format, and a shared trusted Runtime. It proposes and verifies procedures for invoking bounded capabilities; it does not train or store a new neural model.

The product must not be described as a neural network, a trained language model, a model-weight learning system or a replacement for all AI inference.

## Read in order

1. [System architecture and data flow](SYSTEM-ARCHITECTURE-AND-DATA-FLOW.md) — Home, Teacher, Runtime, Kaggle connector, Vault and trust boundaries.
2. [V-Lang and verification model](VLANG-AND-VERIFICATION-MODEL.md) — language format, deterministic execution, tool registration and proof limits.
3. [Current gates, security and research questions](CURRENT-GATES-SECURITY-AND-RESEARCH.md) — GO/NO-GO status, service evidence, unresolved risks and founder questions.

## Critical status at this evidence date

The repository README records VORAX's current GO/NO-GO state as **BLOCKED**. The separate service document dated 21 November 2026 recorded successful HTTP 200 health responses from the Teacher and Runtime services and readability of the existing private Vault. Those were service-health and access observations at that date—not evidence that inference, learning, media generation, or end-to-end promotion was complete.

Kaggle live proof, Teacher discovery/ask verification, Home pull-request learning, media artifact verification and coverage gates were documented as incomplete. A current live health check and a fresh CI run are required before making current service-status statements.

## Non-negotiable distinctions

- A health response is not proof of inference.
- A teacher proposal is not a learned or approved capability.
- A V-Lang test pass proves only the declared test cases under the verifier.
- A CALL TOOL contract stub does not prove a real image/audio/video artifact was generated.
- A Home pull request is not a merged Home method.
- A merged method is not proof that every possible input is correct.
- A listed external model is metadata, not a permission to download its weights or invoke it.
- No child may bypass Runtime to write the Vault or Home's verified method store.

## Scope

This dossier covers VORAX only. It does not merge VORAX into RevelaAI or change RevelaAI's provider/model orchestration. The repository README states that RevelaAI integration remains blocked until the VORAX gate becomes GO.

## Related technical sources

- [Project README](../README.md)
- [V-Lang v1](../VLANG_V1.md)
- [Network and resilience contract](../docs/NETWORK.md)
- [Hugging Face service status notes](../docs/vorax/huggingface-services.md)
- [Core CI workflow](../.github/workflows/ci.yml)
- [Data-only Home gate](../.github/workflows/vorax-data-gate.yml)

## Disclosure control

Do not expose credentials, private service tokens, secret values, signed request headers, hidden repository data or private Vault contents in public documentation. Keep exact service URLs and non-sensitive architectural descriptions separate from secret values and internal access procedures.
