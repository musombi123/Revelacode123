# PR #5 merged-main audit — 2026-11-20

## Repository state

- Repository: `musombi123/Revelacode123`
- Default branch: `main`
- Audited main commit: `82b15988ca1c8d64ef93f93b75bad370901c27a5`
- Commit message: `Merge pull request #5 from musombi123/feat/vorax-free-child-controller`
- PR #5 was merged. Main has the merge commit; the former claim that main was untouched is false.
- Main branch protection is disabled according to GitHub's branch metadata (`protected: false`).
- New blocker branch: `fix/vorax-pr5-blockers`, created directly from the audited main SHA above.
- The old `feat/vorax-free-child-controller` tip is `d78c2b8426041895cfcae1773b2d3a593178a6ea`, which is the second parent of the merge commit. Comparing that branch to main shows main is one commit ahead and the old branch has no unique commits not already merged. Do not continue development on it.

## Existing PR-number collision

PR #6 already exists as a draft:
- Title: `Add Kaggle-local GPU image inference and child routing`
- Branch: `feat/vorax-child-services-gpu-image`
- Head: `92f5bf78aa8d06b0d41a9db3a98e202bd786a4de`
- Base: `main` at `82b15988ca1c8d64ef93f93b75bad370901c27a5`

That draft is not being repointed or closed. A new PR from `fix/vorax-pr5-blockers` will therefore be PR #7.

## Files changed by merged PR #5

1. `.github/workflows/ci.yml` — modified
2. `.github/workflows/deploy-hf-child.yml` — modified
3. `.github/workflows/deploy-kaggle-child.yml` — added
4. `.github/workflows/deploy-koyeb-child.yml` — added
5. `.github/workflows/legend-template-gate.yml` — modified
6. `.github/workflows/vorax-data-gate.yml` — added
7. `.github/workflows/vorax-self-ping.yml` — added
8. `README.md` — modified
9. `kaggle/child/vorax_child.py` — added
10. `scripts/prepare_kaggle_kernel.py` — added
11. `scripts/verify_data_pr.py` — added
12. `scripts/vorax_controller.py` — added
13. `scripts/wait_for_ci.py` — added
14. `tests/test_free_children.py` — added
15. `vorax/api.py` — modified
16. `vorax/github_home.py` — modified
17. `vorax/vault.py` — modified

## CI evidence on the merge SHA

GitHub Actions check runs on `82b15988...` show the `verify` job failed before any step started:

| Run | Attempt | Job | Runner ID | Runner name | Steps | Result |
|---|---:|---:|---:|---|---:|---|
| `44702164804` | 1 | `136172899565` | 0 | empty | 0 | failure |
| `44702164804` | 2 (rerun) | `136205095708` | 0 | empty | 0 | failure |
| `44705150913` | 1 | `136182785694` | 0 | empty | 0 | failure |

The failed-job API has no step records. Downloading job logs returned `BlobNotFound`; there are no Python, test, compile, Docker, or checkout logs to inspect. A rerun of the main-commit job was requested and failed again with `runner_id=0`. This is a runner-allocation failure, not demonstrated application/test failure. Repository YAML cannot provision a GitHub-hosted runner. CI remains BLOCKED pending runner availability or account/Actions support resolution.

The CI workflow uses `runs-on: ubuntu-latest`, installs `requirements.txt`, runs `scripts/verify_library.py`, discovers `tests`, compiles Python sources, then builds the Docker image. None of those steps ran in the failed attempts.

## Merged workflow and runtime findings

- Koyeb, Kaggle and HF deploy workflows are manually dispatchable in main. The Koyeb workflow pins its source to `main` and does not set a commit SHA. The Koyeb Git integration can auto-deploy on branch pushes unless disabled.
- The Kaggle publish workflow uses `--no-run`; publishing a kernel is not evidence of a running GPU session.
- The Kaggle worker currently clones the Home repository's `main` branch at runtime. It does not pin the worker runtime to the source release/tag that was published to Kaggle.
- The HF Docker Space is `Musombi/revelacode-vorax-child`; the separate private Dataset Vault is `Musombi/revelacode-vorax-vault`. The workflow uploads Space source; Vault sync is a separate runtime action.
- The Self-Ping workflow probes configured endpoints and logs status, but a workflow definition is not proof of an HTTP 200 response, a Kaggle GPU session, a running HF Space, or files stored in the Vault.

## Media and live proof

The merged Kaggle worker labels heavy media tasks `blocked_local_gpu_adapter` after metadata discovery. It explicitly does not claim it generated PNG, WAV or MP4. No real artifact, SHA-256, size, decoded media validation or GPU-backed artifact provenance was produced by the merged-main evidence examined here. A separate draft PR #6 has proposed image-only local inference, but it is not merged and has no recorded Kaggle artifact proof. Audio, music and video adapters remain unproven.

No live-service proof was present in the repository/workflow results examined:
- Koyeb: no verified live `GET /health` 200 result
- Kaggle: no actual session log with a GPU device observed
- HF Space: no verified live Space health response
- HF Vault: no verified evidence of at least one V-Lang method and one artifact

## GO / NO-GO

**GO remains BLOCKED.** The merged commit contains architecture, workflows and trust controls; it does not contain live-service or GPU artifact proof. RevelaAI is separate and is not modified by this branch. AWS remains postponed.


## PR #7 branch CI evidence (after opening the draft)

The new branch is `fix/vorax-pr5-blockers`, head `43a9a8952eda25aed1b100a585d1c3e7d3f60fbe`, based on main `82b15988...`. Its current push and pull-request check attempts also failed before any step was assigned:

| Run | Event | Job | Runner ID | Steps | Result |
|---|---|---|---:|---:|---|
| `44713022985` | push | `verify` (`136209248482`) | 0 | 0 | failure |
| `44713032211` | pull_request | `verify` (`136209280663`) | 0 | 0 | failure |
| `44713029013` | pull_request_target | `verify-and-merge-data` (`136209269277`) | 0 | 0 | failure |

These runs provide no test logs and do not establish application-level failure. The draft PR is deliberately not mergeable/ready while this allocation issue persists.


## Phase 1 account selection — confirmed account names, not yet authenticated

The owner supplied these account URLs for the first rollout:
- Kaggle: `https://www.kaggle.com/musombiwilliam` — exact username bound in the Phase 1 publish/controller workflows is `musombiwilliam`.
- Hugging Face: `https://huggingface.co/Musombi` — exact case-sensitive owner is `Musombi`.

Kaggle profile rendering was not readable through the public web fetch in this audit. The account URL confirms the username supplied by the owner, but no authenticated Kaggle API token is connected here. Existing kernels, GPU quota remaining, notebook secrets and current session status therefore remain unverified. Official Kaggle documentation describes a free NVIDIA P100 baseline and a weekly GPU quota around 30 hours, sometimes higher depending on demand/resources:
- `https://www.kaggle.com/docs/efficient-gpu-usage`
- `https://www.kaggle.com/docs/api`

The public Hugging Face profile lists two Spaces, one model and one Dataset:
- `Musombi/mvi-ai-engine` — existing Space; its public page reports Running and identifies a FastAPI backend. Do not overwrite it.
- `Musombi/OmniMind` — existing Docker Space showing the starter page and “No application file”. Do not repurpose it without approval.
- `Musombi/Omnset` — existing public Dataset; its public page currently says it is empty. Do not use it as the VORAX Vault.

The selected VORAX resources remain distinct:
- Private Dataset Vault: `Musombi/revelacode-vorax-vault`
- Optional Docker backup Space: `Musombi/revelacode-vorax-child`

Those exact VORAX resources were not visible in the public profile listing. They may be private or not yet created; an authenticated Hugging Face token is needed to distinguish those states. On first successful Kaggle Vault sync, the runtime creates the dedicated Dataset as private if absent. The Vault client now verifies the repository is private and fails closed before uploading Home methods or artifacts if the existing target is public.

Current Hugging Face official rules state:
- A free personal account may use up to two Gradio ZeroGPU Spaces if it meets eligibility requirements.
- Creating or duplicating a Docker or CPU-basic Gradio compute Space for a personal account requires PRO.
- Free accounts have 100 GB of private storage across Hub repositories, not per Dataset.

References:
- `https://huggingface.co/docs/hub/spaces-overview`
- `https://huggingface.co/docs/hub/main/en/storage-limits`

The account's subscription tier and remaining storage are not publicly visible in the profile, so this audit does not assume PRO or additional private-storage headroom. Phase 1 therefore prioritizes Kaggle compute plus the HF private Dataset Vault. The separate Docker backup Space and Koyeb service are parked for later; no existing HF resources are overwritten.


## Latest Phase 1 branch CI evidence

After binding the Kaggle account and adding the private-HF-Vault guard, the current PR #7 head is `4cb843c7ef92ad53aadb593926d981ccafd65683`. The three associated runs again failed before any job step was assigned:

| Run | Event | Job | Runner ID | Steps | Result |
|---|---|---|---:|---:|---|
| `44716214677` | push | `verify` (`136220171717`) | 0 | 0 | failure |
| `44716224379` | pull_request | `verify` (`136220201939`) | 0 | 0 | failure |
| `44716220581` | pull_request_target | `verify-and-merge-data` (`136220190528`) | 0 | 0 | failure |

No test or code step ran. This repeats the same runner-allocation failure and does not validate or invalidate the new Vault privacy test. CI is still BLOCKED.

## Phase 1 implementation delta

- Kaggle publish/controller workflows now bind the exact username `musombiwilliam`; only `KAGGLE_KEY` remains a GitHub Actions secret.
- Free accelerator default is `NvidiaTeslaP100`, matching Kaggle's documented free baseline; other accelerators must be selected only if the account exposes them.
- The Kaggle Notebook must receive `VORAX_HOME_READ_TOKEN`, `VORAX_HOME_WRITE_TOKEN`, `VORAX_KAGGLE_CHILD_API_KEY`, and `HF_VAULT_TOKEN` as notebook secrets.
- HF Vault sync now checks the resolved Dataset's `private` flag after repository creation/lookup and fails before upload if the Dataset is public.
- The HF Docker Space and Koyeb deployment workflows are manual-only and parked. The HF Docker deployment is not part of Phase 1 unless the user confirms account eligibility for PRO compute.
- The README now prioritizes Kaggle compute plus the HF Dataset Vault, and explicitly protects existing `MVI AI Engine`, `OmniMind`, and `Omnset` resources from accidental overwrite.
