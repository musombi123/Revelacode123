# REVELACODE LEGEND

Code. Compose. Verify. Evolve.

RevelaCode Legend is a research prototype for a deterministic, expandable knowledge engine built around T-Lang templates. It targets repeatable tasks that can be expressed as deterministic procedures; it does not claim to replace every foundation model.

## Quick start

Requires Python 3.11 or later.

Linux/macOS:

    python -m venv .venv
    source .venv/bin/activate
    pip install -e ".[dev]"
    python -m unittest discover -s tests -v
    python -m legend.cli verify-library
    uvicorn legend.api:app --host 0.0.0.0 --port 8000

Windows PowerShell:

    py -3.12 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -e ".[dev]"
    python -m unittest discover -s tests -v
    python -m legend.cli verify-library
    uvicorn legend.api:app --host 0.0.0.0 --port 8000

## API

- GET /v1/health — runtime and capability declaration.
- GET /v1/templates — available procedures.
- POST /v1/compose — execute an exact template ID with typed inputs.
- POST /v1/learn — ask an optional external teacher to propose T-Lang, verify it, and store only the structured procedure locally.
- POST /v1/sync — pull newly merged master templates from Home.
- POST /v1/submit — sign a local template and open a Home pull request.

Example compose body:

    {"template_id":"math.add","inputs":{"values":[10,20,12]}}

The engine returns 42. An unrecognized template ID returns UNKNOWN; there is no general-purpose answer fallback.

## Child environment variables

- LEGEND_ROOT — repository/library root.
- LEGEND_CHILD_ID — unique child identity.
- LEGEND_CHILD_WORKSPACE — child-local template directory.
- LEGEND_CHILD_API_TOKEN — optional bearer token protecting learn, sync, and submit.
- LEGEND_TEACHER_ENDPOINT — OpenAI-compatible Chat Completions endpoint.
- LEGEND_TEACHER_API_KEY and LEGEND_TEACHER_MODEL — optional external teacher credentials.
- LEGEND_GITHUB_TOKEN — repository-scoped GitHub App installation token or prototype fine-grained token for PR creation.
- LEGEND_HOME_READ_TOKEN — optional read-only token for private Home sync.
- LEGEND_GITHUB_REPOSITORY — for example musombi123/Revelacode123.
- LEGEND_GITHUB_BASE_BRANCH — normally main.
- LEGEND_CHILD_HMAC_SECRET — unique secret for this child.

Home GitHub Actions secret:

- LEGEND_CHILD_HMAC_KEYS — JSON object mapping child IDs to HMAC secrets. Never commit it.

## Docker

    docker build -t revelacode-legend .
    docker run --rm -p 8000:8000 -e LEGEND_CHILD_ID=local-child revelacode-legend

## Research boundary

The initial 20 seed templates cover deterministic text, list, and arithmetic operations. They do not constitute broad world knowledge or image/audio perception. An external teacher can help author procedures, but is not part of the execution path. A finite verifier checks declared behavior and test vectors; it cannot establish universal truth from examples.

See docs/architecture.md, docs/tlang-v1.md, and docs/research-roadmap.md.
