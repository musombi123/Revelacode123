# REVELACODE LEGEND

**Legend is a bounded symbolic composition engine, not a neural model.** It stores compact, typed, executable procedures in T-Lang and returns an answer only when a verified procedure applies.

## Current architecture

- **Home:** this GitHub repository is the versioned source of truth for the templates/library directory.
- **Verifier:** legend_core/tlang.py validates a strict data-only DSL, operation allowlist, input types, private-data patterns, and executable test vectors.
- **Composer:** legend_core/engine.py selects a matching procedure and executes it deterministically. Unknown tasks return status=unknown with no guessed answer.
- **Children:** the same API can be deployed independently. Each deployment has a child ID, local SQLite cache, optional teacher credentials, and a GitHub token for proposal PRs.
- **Home gate:** the template workflow runs verifier code checked out from the trusted base commit. It accepts only new T-Lang files from same-repository child branches; it never executes code from a proposal. A passing proposal is squash-merged automatically.
- **CI:** the core workflow checks the committed catalog and runs the cross-child tests.

## Stack choice

Python 3.11+ is used for the research engine and API because the first milestone is formal language design, validation, deterministic interpreters, tests, and integrations—not high-throughput model inference. FastAPI provides the HTTP surface; SQLite keeps the initial deployment light. T-Lang is JSONL so it is inspectable and portable across Python, Rust, Go, or TypeScript children later.

## Run locally

    python -m venv .venv
    # Windows PowerShell: .venv\Scripts\Activate.ps1
    # macOS/Linux: source .venv/bin/activate
    pip install -r requirements.txt
    python scripts/verify_library.py
    python -m unittest discover -s tests -v
    uvicorn legend_api:app --host 0.0.0.0 --port 8000

Open http://localhost:8000/docs for the API.

## First API calls

    curl http://localhost:8000/v1/health
    curl http://localhost:8000/v1/templates
    curl -X POST http://localhost:8000/v1/compose \
      -H "Content-Type: application/json" \
      -d '{"task":"add","inputs":{"a":12,"b":30}}'

A task the library cannot prove produces status unknown; the engine does not invent a response.

## Learning from ChatGPT or another teacher

There are two supported routes:

1. **Teacher API:** set LEGEND_TEACHER_BASE_URL, LEGEND_TEACHER_API_KEY, and LEGEND_TEACHER_MODEL on a child service. The /v1/learn/teacher endpoint asks an OpenAI-compatible endpoint for one T-Lang proposal, then validates it and executes its test vectors. This is an API integration; it does not read a user's ChatGPT browser conversation or use a ChatGPT subscription as an API key.
2. **ChatGPT/manual bridge:** ask ChatGPT to produce one object following /v1/tlang/spec, then submit that object to /v1/learn/import. The raw prose is not stored; only an accepted T-Lang record is stored.

Teacher output is untrusted. Passing tests only proves the tested procedure against the declared tests; it does not establish universal truth. Human review remains recommended before automatic publication for high-impact domains.

## Deployment environment

| Variable | Purpose |
|---|---|
| LEGEND_HOME_REPO | Home repository; defaults to musombi123/Revelacode123 |
| LEGEND_HOME_BRANCH | Home branch; defaults to main |
| LEGEND_CHILD_ID | Unique child identity for this deployment |
| LEGEND_CHILD_API_KEY | Secret for child-authenticated learning, sync, and submit |
| LEGEND_ADMIN_API_KEY | Optional control-panel key; never put this in frontend source |
| LEGEND_GITHUB_TOKEN | Fine-grained token with Contents + Pull requests write access to Home |
| LEGEND_TEACHER_BASE_URL | OpenAI-compatible API base URL |
| LEGEND_TEACHER_API_KEY | Teacher API secret |
| LEGEND_TEACHER_MODEL | Teacher model/deployment identifier |
| LEGEND_DB_PATH | SQLite path; use a persistent disk in production |
| LEGEND_CORS_ORIGINS | Comma-separated allowed frontend origins |

Never expose GitHub or teacher API keys in Vite variables or browser JavaScript. Keep them in deployment secrets.

## Devouring Physics v3

Legend now has bounded Hugging Face metadata discovery, modality tags, conduct prompts, a candidate-import verifier, and a Gemini teacher import bridge. See [DEVOUR_PHYSICS_V3.md](docs/DEVOUR_PHYSICS_V3.md) for API contracts, configuration, and modality limits.

- `POST /legend/generate` is the RevelaAI-first route; verified templates answer, unknown remains unknown.
- `POST /v1/devour/discover` catalogues metadata by modality without downloading weights.
- `POST /v1/devour/conduct` returns a method-only teacher prompt.
- `POST /v1/devour/import` accepts a bounded T-Lang candidate from Gemini, executes its declared test vectors, then submits to Home.
- `GET /v1/devour/stats` reports the bounded catalog view and verified/rejected import counters.

## Important limits—no false claims

- GitHub is durable source control and an event-driven verifier, not an always-running cognitive process. A child API/worker must run on a host; GitHub Actions runs when events occur.
- T-Lang templates are reusable algorithms and mappings, not model weights. They can replace a model only for tasks that have explicit, verified procedures.
- A language model cannot transfer its weights or all general capabilities into a short rule template. Vision, audio perception, broad language understanding, and open-ended reasoning require specialised deterministic algorithms, external tools, or a model service.
- Passing self-authored tests can still be wrong or incomplete. The verifier checks syntax, bounded operations, safety patterns, duplicates, and declared examples—not mathematical proof of every possible input.
- Automatic PR merge is limited to data-only library additions. Core code, verifier, workflows, and API changes are not auto-merged by the child gate.
- The first milestone supplies 20 deterministic seed templates and a tested two-child reuse simulation. Live ChatGPT teaching and cloud deployment require the relevant API and deployment secrets to be configured.
