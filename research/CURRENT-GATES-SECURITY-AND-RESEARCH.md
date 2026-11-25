# VORAX — current gates, security and research questions

**Evidence snapshot:** 25 November 2026  
**Sources:** repository README, `docs/vorax/huggingface-services.md` (dated 21 November 2026), `docs/NETWORK.md`, GitHub workflows and source tree.

## 1. Current GO/NO-GO status

The inspected repository README records:

<blockquote>
GO / NO-GO: BLOCKED
</blockquote>

This is the authoritative project status for this dossier unless a newer, reviewed status record supersedes it.

The service document dated 21 November 2026 recorded:
- HTTP 200 health responses from the Teacher and Runtime Spaces;
- access to the existing private Vault;
- incomplete live proof for Kaggle cycle, Teacher discovery/ask, Home PR learning flow, media artifact verification and coverage gates.

A health check proves that a service returned its health response at that time. It does not prove the full inference, learning, verification, artifact generation or promotion cycle. This dossier did not conduct a fresh external live test on 25 November.

## 2. Why the gate remains blocked

The system requires more than separate services returning healthy responses. Before GO, the team needs reproducible evidence that:

1. a request enters through the canonical Input X path;
2. Home lookup and deterministic method execution work;
3. an unknown task is handled safely;
4. Teacher discovery and proposal return a valid candidate;
5. Runtime authenticates and validates the candidate;
6. declared test vectors execute correctly;
7. the pending Vault record, provenance and checksum are written;
8. the isolated Home pull request is created;
9. trusted CI and human review determine acceptance;
10. the approved method becomes available through the permitted runtime path;
11. real registered tool outputs pass file/artifact verification where the method invokes tools;
12. failures, retries, duplicates and service outages are handled without bypassing trust boundaries.

The specific current completion evidence must be recorded in the gate report. Do not infer GO from a single passing test, a 200 health response or a deployment workflow completing.

## 3. Trust boundaries

The documented architecture assigns authority as follows:

- **Home:** authoritative approved method library and metadata index.
- **Teacher:** remote discovery/inference and candidate proposal; untrusted until verified.
- **Kaggle Hunters:** isolated clients; cannot write Home or verified Vault data.
- **Runtime:** trusted validator and coordinator; only permitted component to write pending Vault records and create Home pull requests.
- **Vault:** existing private Dataset for pending/verified records, hashes, reports and independently verified artifacts.
- **GitHub review/CI:** final validation boundary for Home changes.

No child should receive direct credentials to write Home main or the Vault verified namespace. Do not weaken the trust model to make a demo appear successful.

## 4. Network resilience

The network contract documents:

- HTTPS-only control-plane requests;
- bounded request timeouts and retry attempts;
- exponential retry delays;
- request ID and response metadata logging without secret headers/payloads;
- Runtime/Teacher circuit breaker;
- periodic peer health checks;
- idempotent candidate/report writes;
- safe pause/resume behavior when a peer is down.

A peer-health probe is not an inference or learning benchmark. A retry is safe only when it cannot create duplicate methods, overwrite a different Hunter's report or create inconsistent Vault/Home state.

## 5. Model and data restrictions

VORAX is deliberately weight-free. The checked-in Vault validator rejects model-weight payload types and model architecture configuration files. It also verifies that the existing Vault is private and refuses to create a replacement if it cannot safely open the expected one.

Do not add:
- model weights or checkpoints;
- hidden model architecture configurations;
- unrestricted code execution;
- arbitrary shell/network/file tool operations;
- personal data, credentials or raw chat transcripts in method provenance;
- unverified assets in the trusted artifact namespace.

Teacher responses and source material are untrusted data. They are not instructions to the Runtime and do not gain privilege by being stored as JSON.

## 6. CI and release evidence

The core CI workflow is defined to:
- verify the Home method library;
- check the metadata-only Home index;
- run unit and cross-child tests;
- compile Python source;
- build the portable child container.

The VORAX data gate is designed to run the trusted verifier from the base commit, require core CI for the exact PR head commit and only then auto-merge eligible data-only changes. Human approval and review of the resulting evidence remain part of the Home governance process described by the project.

The presence of workflows is not proof that the current main commit has a green CI run. Capture the exact commit SHA, workflow run, test summary and release manifest when preparing an external technical pack.

## 7. RevelaAI integration boundary

The repository states that RevelaAI remains on its existing Gemini/provider path and that VORAX integration is not to proceed until the VORAX GO/NO-GO state changes to GO.

That separation must remain in place. Do not claim RevelaAI is currently powered by VORAX, that its provider calls are already replaced, or that VORAX has reduced inference spending without verified telemetry.

## 8. Security and operational tests still needed

- Candidate fuzzing and malformed-record tests.
- Prompt-injection and malicious source-content tests.
- Secret/PII leakage tests across provenance, reports and logs.
- Runtime child-authentication and key-rotation tests.
- Vault private-state and forbidden-weight scanning.
- Duplicate Hunter, retry and idempotency tests under concurrent submission.
- Home unavailable / Teacher unavailable / Runtime unavailable / Kaggle unavailable simulations.
- Real artifact generation and decode/format tests for each external adapter.
- Artifact hash and source-manifest reconciliation.
- CI permission review, pull_request_target risk review and trusted base-code verification.
- Fresh live service health and end-to-end learning cycle evidence.

## 9. What VORAX can defensibly claim now

A careful explanation:

> VORAX is a constrained capability-learning system built around a deterministic Input X path, a typed V-Lang procedure format and a trusted Runtime. It can validate bounded procedure candidates against declared tests and controlled tool contracts, while keeping Home approval and private Vault writes behind explicit trust boundaries. It is not a model-training system. Its GO gate remains blocked until the end-to-end learning, promotion and artifact-verification evidence is complete.

## 10. Commercial and research questions

Before making an investment or research-partnership case, answer:

1. What problem does VORAX solve better than ordinary function calling or workflow engines?
2. Which tasks are actually covered by approved procedures?
3. What is the measured success rate for known tasks and safe unknown handling?
4. How many candidate proposals are rejected, and why?
5. What is the cost and latency of Teacher inference per candidate?
6. What percentage of requests complete without Teacher inference?
7. Which tools have real verified adapters, not only schemas?
8. How are methods versioned, reviewed, deprecated and rolled back?
9. How is correctness established for domains where wrong output can cause harm?
10. What independent evaluation would distinguish VORAX from a rule engine, plugin registry or orchestration framework?

These are research questions, not pre-existing performance claims.
