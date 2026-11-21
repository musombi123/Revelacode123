# VORAX Permanent Weekly Knowledge-Growth Cycle

## Purpose

VORAX acquires verified procedures, not models. Remote model weights, checkpoints,
architectures and local inference packages are never downloaded. Remote Teachers
remain at their providers. Paid inference is disabled by default.

The weekly cycle is a permanent operating loop. It does not stop after the first
seven-day campaign.

## Schedule and orchestration

The repository workflow is .github/workflows/vorax-weekly.yml:

- Monday 02:00 UTC: begin or resume the ISO-week cycle.
- Daily 03:00 UTC: retry overdue workers, recover cached reports and retry Home
  report/method pull requests.
- workflow_dispatch: manual coordination for the current ISO week.

The daily coordinator reconciles manifests from prior ISO weeks, retrieves
undelivered reports, resumes due 24-hour retries, and dispatches queued hunters
from all active cycles. It prioritizes retries before new-cycle work and launches
at most three kernels per coordinator run.

### Required scheduler credentials

Configure these repository Actions settings before the scheduled workflow can
launch Kaggle Children:

- Secret VORAX_KAGGLE_CHILD_API_KEY: the existing Runtime/Teacher child key.
- Secret KAGGLE_API_TOKEN: a Kaggle API token for the hunter account.
- Variable KAGGLE_USERNAME: the Kaggle account name.
- Optional variable VORAX_RUNTIME_URL: defaults to the current Runtime HF Space.

Also configure the Kaggle account Secret VORAX_KAGGLE_CHILD_API_KEY for
the kernel process itself. VORAX_HOME_READ_TOKEN is optional in the Kaggle
kernel because Runtime can serve its validated metadata-only cached index.
The workflow never reads or forwards VORAX_X.

GitHub Actions performs coordination only. It creates or finds a Home tracking
issue, calls Runtime POST /vorax/cycle/start, and pushes bounded Kaggle kernels.
It does not perform model inference or write methods into Home.

Cycle identity is ISO week format YYYY-Www. Runtime persists the cycle manifest
in the existing private HF Dataset Vault under reports/weekly/manifests/.

## Hub and Hunter Mesh

### Home Hub

Home (musombi123/Revelacode123, main) is the source of truth for the approved
method library and metadata index. Children read the metadata-only index; they
never download Home method bodies. Home does not run learning and does not teach
Children. Weekly reports and methods enter Home only through isolated pull
requests. The main branch ruleset and required verify check remain authoritative.

Runtime also keeps a validated metadata-only Home index snapshot in the existing
private Vault. It is an explicitly marked stale fallback if GitHub is unavailable;
it is not a replacement for Home and contains no method bodies.

### Teacher and Runtime gateway pair

Teacher owns bounded discovery and remote Teacher requests. Runtime owns Child
authentication, candidate verification, Vault pending writes, SHA-256
deduplication, and isolated Home PR creation. Kaggle Children have no Vault write
credential and cannot write to Home.

Teacher and Runtime poll one another's public health endpoint every 60 seconds.
Peer outages are reported and cause pause/retry behavior; no component reports
health or a learning result it has not observed.

### Kaggle Hunter Mesh

Runtime creates up to three separate, private, GPU-disabled, Internet-enabled
Kaggle kernels:

| Hunter | Slice | Responsibility |
|---|---|---|
| hunter-1 | text-docs | Metadata discovery and the assigned deterministic V-Lang curriculum task |
| hunter-2 | vision-audio | Metadata discovery, modality classification and missing-capability report |
| hunter-3 | video-tools | Metadata discovery, tool/API capability classification and missing-capability report |

Hunters do not communicate with one another. Each communicates with Runtime only.
Each kernel has a hard 7,200-second budget. A completed report is written to
/kaggle/working/vorax-learning-report.json before Runtime delivery is attempted.
If delivery fails, the next coordinator run retrieves the private kernel output
and retries the report through Runtime. Kernel IDs include ISO cycle and hunter
attempt, so attempt 1 cannot overwrite attempt 0's output before recovery.

## Bounded discovery

The discovery gateway retains the PR #24 limits:

- 20 metadata IDs per page.
- No more than 20 pages per source per pass; the weekly connector defaults to 3.
- No more than 5 concurrent model metadata lookups.
- Model-specific /v1/models/{model_id} metadata requests only.
- No bulk router catalogue, cardData expansion, model file downloads or weights.
- Public model, Space and Dataset metadata only.

A page cap is a successful bounded pass, not a reason to claim that the full Hub
was catalogued. Remaining pagination is recorded for later cycles.

## Weekly procedure acquisition

Runtime selects one unlearned deterministic curriculum task from the versioned
task list, after checking Home's metadata index. The currently assigned task is
separate from VORAX_X. Weekly cycle mode never reads VORAX_X or its Kaggle secret.
The active pricing.final_after_discount_tax task is therefore not rerun or
modified by this scheduler.

The Text + Docs Hunter asks up to five distinct live free-provider text Teachers
to propose and review the assigned V-Lang method. Runtime requires exact task
identity, input schema, triggers and required test vectors, then executes the
candidate tests. It writes only verified candidates to the existing Vault
pending area and creates an isolated Home PR.

The reviewed seed curriculum currently contains 27 deterministic procedures.
After those seeds are covered by Home and Vault pending records, Runtime generates
new numeric-composition tasks from the six registered arithmetic operators
(add, subtract, multiply, divide, percentage-of and percentage-change). Generated
chains contain 2–11 operations, use no more than 12 numeric inputs, and include
three independently calculated reference vectors. The grammar contains
435,356,460 distinct ordered operator chains before repeating a chain shape.
Generated tasks are also de-duplicated by Home metadata and Vault pending task
and trigger coverage. This prevents the weekly curriculum from stopping when
the initial seed set is exhausted.

Vision/audio and video/tools hunters currently perform metadata discovery and
produce missing-X priorities. The current Teacher gateway is text-oriented, and
the existing CALL TOOL test executor is a contract stub. Runtime therefore
rejects weekly CALL TOOL acquisitions until a real remote artifact/evidence path
exists. This is deliberate: metadata discovery or a contract stub is not proof
that image, audio or video inference ran successfully. Those adapters can be
added later without weakening verification.

## Reports and state

A final report is stored in the Vault and submitted to Home through a report-only
PR at reports/weekly/YYYY-Www.json. It includes:

- discovered_count and understood_count;
- acquired_count, verified_count and added_count;
- rejected_count and individual rejection reasons;
- missing_X_list and next_priority;
- hunter IDs, discovery slices, attempts and completion states;
- explicit no-weights, no-local-model and no-billable-inference markers.

Counts have strict meanings: a method is acquired only after Runtime accepts its
candidate; verified only after Runtime tests pass; added only after Home confirms
the relevant merge/promotion. Discovery-only workers cannot inflate method counts.
A final report is not emitted as complete if a hunter report is missing. Runtime
records a bounded timeout/dispatch failure report when a worker cannot produce one.

Prior methods are never overwritten. If Home already covers a task, Runtime
selects the next uncovered curriculum item. SHA-256 plus ISO cycle ID makes
proposal submissions idempotent across hunters.

## Failure and retry policy

- Teacher unavailable: Runtime pauses new hunter dispatch until Teacher health
  recovers; daily coordinator runs recheck it.
- Runtime unavailable: running hunters save reports and any candidate evidence
  to private Kaggle kernel output; the coordinator retries delivery later. A
  new worker is not launched while Runtime cannot create/update its manifest.
- GitHub/Home unavailable: Runtime keeps verified methods and weekly reports in
  Vault pending state. Home PR submission is retried by later coordinator runs.
  The Home metadata index may be read from the Runtime's clearly marked cached
  Vault snapshot.
- Kaggle dispatch unavailable: Runtime records a failed dispatch. The cycle
  follows the one-retry-after-24-hours policy.
- One retry maximum: after the retry fails, Runtime marks the cycle failed and
  retains its report and reasons. The next weekly cycle continues independently.

A missing report, unavailable peer or network exception is never recorded as
success.
