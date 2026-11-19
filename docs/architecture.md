# RevelaCode Legend — Home + Children

## Research objective

Legend is a deterministic template-composition system. It stores executable procedures in T-Lang, runs them through a restricted interpreter, checks candidate procedures with test vectors, and synchronizes signed child proposals into a GitHub-hosted master library.

The runtime does not contain a foundation model. An optional external teacher can propose a T-Lang procedure. The teacher is not trusted: every candidate must pass local and Home verification.

## Home

- GitHub repository is the versioned source of truth.
- library/seeds.json contains the initial deterministic procedures.
- library/templates/ contains the admitted master library.
- library/trust/children.json contains child IDs blocked by policy.
- scripts/verify_pr.py verifies proposal data without executing code from a PR.
- The Home workflow verifies signed child proposals and auto-merges passing same-repository PRs.

GitHub Actions is event-driven, not a 24/7 compute server. Home is durable storage and an admission authority, not a continuously running brain process.

## Children

Each child runs the same Python package with its own workspace and identity. Children can be deployed as containers on Hugging Face Spaces, AWS, or another Docker host.

- Local library: seed procedures, synchronized master templates, and child-local proposals.
- Composer: deterministic T-Lang interpreter.
- Verifier: schema, operator, reference, privacy, duplicate, and test-vector checks.
- Teacher adapter: optional OpenAI-compatible API used only to propose a T-Lang procedure.
- Submission client: signs T-Lang and opens a GitHub PR.

## Data flow

1. A research task is sent explicitly to an external teacher.
2. The teacher is instructed to return one T-Lang JSON template.
3. The child parses the candidate in memory. It does not persist the raw teacher response.
4. The child verifier runs the candidate test vectors and rejects unsupported operations or unsafe content.
5. A passing template is saved locally as T-Lang only.
6. The child signs the proposal with its child-specific HMAC secret and submits a GitHub PR.
7. Home verifies signature, trust status, schema, test vectors, and duplicate fingerprint.
8. Home auto-merges passing same-repository submissions.
9. Children call the sync endpoint to pull the new master library.

## Security model

- Use GitHub App installation tokens scoped to this repository with Contents and Pull Requests write access. Fine-grained PATs are acceptable for a limited prototype, but are less suitable for long-lived children.
- LEGEND_CHILD_HMAC_KEYS is a GitHub Actions secret containing a JSON mapping from child ID to HMAC secret.
- Each child receives only its own LEGEND_CHILD_HMAC_SECRET.
- Do not put secrets in source control, Docker images, templates, teacher requests, or logs.
- The Home workflow uses pull_request_target because it needs merge permission. It checks out only the trusted base branch and reads proposal files as JSON through Git. It never checks out or runs child-provided code.
- Only newly added library/templates/*.json files are accepted.
- The child engine pauses locally after 100 consecutive rejected proposals. Home-side persistent reputation and distributed rate limiting still need a durable shared counter; the current Home blocklist is an administrative override, not automatic global blocking.

## What verification establishes

A PASS establishes that the candidate follows the T-Lang schema and allowed operator set; references point only to declared inputs or earlier steps; detected email/credential patterns and forbidden raw-output fields are absent; test vectors execute and match expected outputs; the signed submission came from a configured child; and the procedure fingerprint is not already in the known library.

A PASS does not prove that an external teacher's factual premise is true for every possible input. Tests are finite. The DSL is deliberately small. A verifier cannot infer universal truth from a handful of examples.

## Multimodal boundary

The current version has text and numerical procedures only. It cannot perceive pixels, decode audio, watch video, or create general software. Vision/audio children need explicit modality adapters and licensed or user-authorized inputs. Any extracted procedure must still reduce to an allowed, testable T-Lang program. Storing a label or a procedure is not the same as storing a modality's perceptual capability.

## Unknown policy

A request without an exact verified template returns UNKNOWN or not_established. It must not fabricate an answer, silently select a vaguely similar procedure, or claim general knowledge.
