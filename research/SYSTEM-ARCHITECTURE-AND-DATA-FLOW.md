# VORAX — system architecture and data flow

**Evidence snapshot:** 25 November 2026  
**Primary sources:** `README.md`, `vorax/api.py`, `vorax/input_x.py`, `vorax/engine.py`, `vorax/home_index.py`, `vorax/teacher.py`, `vorax/vault.py`, deployment folders and `docs/NETWORK.md`.

## 1. What VORAX is

VORAX is a learning engine for verified capability-invocation procedures. It attempts to represent a bounded task as a typed V-Lang procedure and execute that procedure through a constrained runtime.

It does not learn by changing neural weights. It does not contain an embedded foundation model. It does not claim that a successful test proves unrestricted intelligence or general correctness.

The architectural invariant is:

**one Input X → one constrained V-Lang method format → one shared trusted Runtime.**

## 2. Logical service topology

```text
                         GITHUB HOME
             authoritative approved V-Lang source
                + metadata-only method index
                            |
                            | metadata / review workflow
                            v
KAGGLE LEARNING CONNECTOR ---> TEACHER SERVICE
     isolated Hunter          remote teacher discovery
     and cycle client          and candidate proposal
             |                         |
             | candidate/report        | candidate method
             +------------+------------+
                          |
                          v
                    SHARED RUNTIME
              authentication + verification
              bounded execution + coordination
                          |
              +-----------+------------+
              |                        |
              v                        v
       PRIVATE VAULT             HOME PR WORKFLOW
       pending methods,           isolated proposal
       checksums, reports          for review/CI
              |                        |
              +------------+-----------+
                           |
                  Home review + trusted CI
                           |
                           v
                APPROVED HOME METHOD
                           |
                           v
              trusted runtime method catalogue
```

This is the documented logical topology. It is not proof that every remote service is currently reachable or that all end-to-end flows have passed live testing.

## 3. Component responsibilities

### 3.1 GitHub Home

Home is the authoritative source and review boundary for approved V-Lang methods. Its metadata-only index lists method identifiers, domains, tasks, triggers and SHA-256 fingerprints. It is designed to help clients discover available methods and avoid duplicate proposals without copying executable method bodies into untrusted child environments.

Home's main branch is not written directly by Kaggle Hunters or Teacher services. A candidate reaches Home through a pull request created through the trusted Runtime path and the repository's review/CI gates.

### 3.2 Input X

Input X is the single intake path. `vorax/input_x.py` bounds input length, uses the canonical deterministic V-Lang `input.classifier` method from the available catalogue and resolves a task class into a method/tool mapping.

There is no neural classifier fallback in the inspected source. If the classifier or a suitable verified method is missing, VORAX returns an unknown state. Unknown is a valid and important outcome: the system must not invent an answer to disguise missing capability.

The inspected code limits `code.execute` to bounded arithmetic-expression input; it is not a general code-generation or shell-execution facility.

### 3.3 Teacher service

Teacher is a remote discovery/proposal component. It can discover or query configured remote inference providers and propose a candidate structured V-Lang method. The teacher is not the trusted verifier and cannot approve its own proposal.

Teacher output, retrieved pages and model metadata are treated as untrusted input. A candidate must pass V-Lang parsing, type rules, operation/tool allowlists and test execution in the trusted verification path.

The configured Teacher adapter is an OpenAI-compatible remote API client. It does not imply that VORAX owns or trains the external model used by that endpoint.

### 3.4 Kaggle Learning Connector

The Kaggle component is an isolated learning-cycle client/Hunter. The repository docs specify that it works from Home metadata and remote Teacher services. It does not clone the approved Home library, download Home method bodies or model weights, write directly to the Vault's verified namespace, or communicate directly with another Hunter.

Kaggle scheduling and proof of the full live cycle remain separate verification gates.

### 3.5 Shared Runtime

Runtime provides the trusted execution and verification boundary. It authenticates children/services, validates candidate procedures and controls writes to pending Vault records and Home pull requests.

Its health endpoint can report service identity, template counts, Home reference and configuration state. A health payload does not prove that a teacher inference request or a real registered media adapter succeeded.

### 3.6 Private Vault

Vault is an existing private Hugging Face Dataset used for pending method submissions, checksums, version information, reports and independently verified artifacts. The code reads the existing private Dataset and fails closed if it is missing, public, or contains forbidden model payloads/architecture files.

Vault is not intended to become a model-weight repository. The inspected code rejects model weight formats and model architecture configuration patterns.

## 4. Data and proposal lifecycle

A typical learning attempt is:

1. A task arrives through Input X or an authorized learning cycle.
2. VORAX checks the approved method catalogue.
3. If no suitable method exists, a Teacher may be asked to propose a bounded V-Lang candidate.
4. The candidate is validated against the V-Lang schema and privacy checks.
5. The trusted Runtime executes the declared test vectors.
6. The Runtime checks registered tool names and argument contracts.
7. A passing candidate may be written to the Vault pending area with provenance and checksum.
8. Runtime creates a Home pull request through the isolated review path.
9. Trusted Home CI and human review decide whether the proposal is accepted.
10. Only after verified source and hash reconciliation can it enter the approved method workflow.

Not every step is currently proven end-to-end at the evidence date. The repository's status material marks the overall GO gate BLOCKED.

## 5. State separation

The architecture uses separate states for:

- metadata discovery;
- teacher candidate generation;
- syntax/schema validity;
- passing declared test vectors;
- pending Vault submission;
- Home PR review;
- approved Home method;
- available runtime capability;
- verified real artifact.

These states must never be collapsed into one generic "learned" badge. In particular, discovery does not equal learning, learning proposal does not equal verification, and verification of a bounded method does not prove universal correctness.

## 6. Deployment and secrets

The repository defines GitHub Actions workflows and remote Hugging Face service deployment materials. Credentials are expected to be held in the relevant platform secret stores. VORAX network documentation requires HTTPS, request timeouts, bounded retries, request IDs without secret payloads and a circuit breaker between Runtime and Teacher.

Never place token values in source files, examples, CI output, reports or public research. Share only variable names and responsibility descriptions when explaining deployment configuration.

## 7. Current repository map

| Path | Responsibility |
|---|---|
| `vorax/input_x.py` | Canonical input intake/classification and source discovery |
| `vorax/engine.py` | Deterministic procedure selection and composition |
| `vorax/tlang.py` / `vorax/vlang.py` | Language parser, validation and execution contract |
| `vorax/teacher.py` | Remote OpenAI-compatible teacher candidate adapter |
| `vorax/home_index.py` | Metadata-only Home method index |
| `vorax/hf_runtime.py` / `vorax/hf_teacher.py` | Service integration modules |
| `vorax/vault.py` | Private Vault validation and storage boundary |
| `vorax/network.py` | Bounded network policy |
| `vorax/weekly_cycle.py` / `vorax/scheduler.py` | Learning-cycle coordination |
| `deploy/` | Remote runtime/teacher service packaging |
| `templates/library/` | Home procedure templates and metadata |
| `tests/` | Input, language, Runtime, Teacher, Vault and cycle verification |

## 8. Key architectural questions

- What exact method bytes and hash were executed for a particular answer?
- Which method catalogue version was used?
- Did execution require a remote tool, and did the real artifact pass validation?
- Was the candidate produced by Teacher, written to pending Vault, submitted as a PR, reviewed, and merged?
- Can the same request be retried without duplicate Vault or Home records?
- What is the correct behavior when Home, Teacher, Runtime, Kaggle or Vault is unavailable?
- Can an untrusted candidate cause arbitrary code, shell, network or filesystem access?

These questions should be answerable from request IDs, method hashes, verification reports and immutable stage records.
