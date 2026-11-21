# VORAX Network and Resilience Contract

## Trust boundaries

Home is the authoritative approved method library. Teacher is a remote discovery
and inference gateway. Runtime is the only service allowed to write Vault pending
records or create Home pull requests. Kaggle Hunters are isolated clients. No
Hunter communicates directly with another Hunter.

Secrets remain in GitHub Actions Secrets/Variables, Hugging Face Space Secrets,
or Kaggle Secrets. Credentials, authorization headers and secret values are
never included in logs or reports.

## Request policy

Control-plane and metadata calls use:

- HTTPS only.
- 30-second maximum request timeout.
- At most 3 attempts.
- Exponential retry delay (1 second, then 2 seconds).
- Request ID, UTC timestamp, HTTP method, destination host, status and attempt
  recorded without request headers or payload secrets.
- JSON response size bounded to 3 MB for VORAX gateway requests.

The Runtime/Teacher gateway uses a circuit breaker: 5 failures in a rolling
10-minute window opens the circuit for 60 seconds. A half-open probe determines
whether requests may resume. A health probe is not treated as proof of inference
or learning.

The remote provider may have its own execution latency and availability limits;
a remote response is still subject to VORAX's bounded gateway and verification
policy.

## Peer health

Runtime polls Teacher /health every 60 seconds. Teacher polls Runtime /health
every 60 seconds. The health payload identifies the peer and reports the last
observed state. If a peer is down, the system pauses dependent work and queues
or caches recoverable reports; it never substitutes a fabricated successful
response.

## Idempotency and persistence

Method proposal deduplication is stored by method SHA-256 plus ISO cycle ID in
the Runtime-owned Vault ledger. The first Hunter claim is authoritative; a
different Hunter receives a duplicate result. Retries from the same Hunter are
idempotent.

Weekly manifests, per-Hunter reports and the aggregate report are stored in the
existing Vault under reports/weekly/. The Home metadata cache is metadata-only
and is also written by Runtime. Children do not receive Vault write credentials.

## Degraded network behavior

### Teacher down

Runtime pauses cycle dispatch. It keeps the cycle manifest and resumes queued
Hunters only after the Teacher peer is healthy.

### Runtime down

Already-running Hunters persist their JSON output locally in Kaggle's private
kernel output. The next coordinator run retrieves the output and retries
/vorax/cycle/hunter-report. New kernels are not launched until Runtime can
authenticate and persist the cycle state.

### GitHub down

Runtime-verified procedures remain in Vault pending state. Home PR creation is
retried on later coordinator runs. A validated Home metadata snapshot in Vault
allows bounded discovery to continue when GitHub is temporarily unavailable;
the snapshot is marked stale and never treated as a fresh Home read.

### Kaggle down

No child is marked running unless the Runtime accepted the status transition.
A failed push is recorded as a failure, with one retry after 24 hours.

No failure path writes directly to Home main or the Vault verified namespace.

## Scheduler activation settings

The GitHub Actions workflow requires repository secret
VORAX_KAGGLE_CHILD_API_KEY, repository secret KAGGLE_API_TOKEN, and
repository variable KAGGLE_USERNAME. The Kaggle account separately needs
its secret VORAX_KAGGLE_CHILD_API_KEY; the Home read token is optional because
Runtime can serve its metadata-only cache. No VORAX_X secret is read in weekly
cycle mode.
