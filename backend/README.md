# Product feedback backend

FastAPI API and durable MongoDB workers for cached product summaries, reviews,
scoped legacy analysis, PM corrections, and grounded questions. Product route IDs
are `parent_asin` values (`products._id`).

Incremental summaries use a dedicated authenticated local Qwen llama-server
client pinned to `qwen3-4b-instruct-2507-local`. Only the model API is tunneled
through ngrok; the backend stays local. Legacy analysis retains its separate
Groq Free client. No provider or model fallback is automatic. One authorized
synthetic legacy extraction succeeded on 2026-09-27,
returning one locally validated finding and citation. This proves connectivity
and the JSON contract, not general semantic quality or remaining account quota.
Keep the Groq account on **Free**; never enable a paid plan or automatic billing.
The endpoint/model allowlist cannot prevent charges on an upgraded account.
See [provider contract](../docs/provider-contract.md).

## Setup and processes

Tested with Python 3.14.7; supported Python range is 3.12–3.14. Direct versions are
pinned in `pyproject.toml`: FastAPI 0.141.1, PyMongo 4.18.2, Pydantic 2.13.5,
HTTPX 0.28.1, Hindsight client 0.10.1, Uvicorn 0.54.0, pytest 9.1.1.
Use the backend virtualenv separately from the importer environment:

```sh
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install -e '.[dev]'
```

Set server environment values from the root `.env.example`; the app does not load
`.env` automatically. Keep the existing root secrets file private and unchanged.
Required: `MONGODB_URI`, `MONGODB_DATABASE`, distinct `DEMO_REVIEWER_TOKEN` and
`DEMO_PM_TOKEN`. Memory additionally needs `HINDSIGHT_API_URL` and
`HINDSIGHT_API_KEY`. Incremental summaries require server-only `LOCAL_MODEL_API_URL` and
`LOCAL_MODEL_API_KEY`; `LOCAL_MODEL_NAME` is pinned to the Qwen alias. The
default `SUMMARY_LLM_PROVIDER=local`; explicit `groq` selection uses
`GROQ_API_KEY`. Legacy analysis uses Groq unless an explicit higher-priority
`LLM_API_KEY` is set.
`OPENCODE_API_KEY` and `DEEPSEEK_API_KEY` are ignored. A missing key leaves the API
available with model operations disabled. Endpoint/model overrides are rejected
unless exactly `https://api.groq.com/openai/v1` and `openai/gpt-oss-20b`.
No transport retry or fallback can select another model or provider. An absent local model URL or key leaves cached summary reads available but
records attempted updates as failed for later retry.

Apply the additive v2 migration explicitly before serving writes. It checks known
validators/indexes and refuses incompatible data; it does not drop collections or
rewrite imported raw text/IDs. Back up the target database and inspect the dry run.
The application database migration was applied and verified on 2026-09-27; see
[verification report](../docs/migration-v2-report.json). For another database, use
these operator commands with its exported environment:

```sh
.venv/bin/python -m app.migrations.v2 --dry-run
.venv/bin/python -m app.migrations.v2
.venv/bin/python -m app.migrations.v3 --dry-run
.venv/bin/python -m app.migrations.v3
.venv/bin/uvicorn app.main:configured_app --factory --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 5
```

Start the separate worker in a second terminal with the same environment:

```sh
.venv/bin/python -m app.run_worker
```

Start the dedicated incremental summary worker in a third terminal with the same
server-only environment:

```sh
.venv/bin/python -m app.run_summary_worker
```

V3 is a separate explicit migration. It adds state, immutable versions, and input
membership without rewriting v2 raw reviews or legacy reports. The summary worker
updates a product after its generated draft is accepted as the initial published
summary. No separate semantic validation is required by the current product policy.
Run the v4 validator migration and `app.summaries.publish_drafts --apply --accept-drafts`
after staging initial drafts; acceptance preserves provenance without claiming
independent factual validation. See the
[cutover report](../docs/summary-cutover-report.md) before running this against the
application database.

## Cached summary contract

`GET /api/v1/products/{product_id}/summary` returns an explicit `uninitialized`
view before publication. GET summary, history, and individual versions never
enqueue generation or call a model. PM-only routes include paginated
`/summary/history`, `/summary/versions/{version}`, `PATCH /summary/settings`,
`POST /summary/refresh` (required `Idempotency-Key`), and
`POST /summary/questions` bound to an immutable published version. Reviewer
submission/status responses expose a separate summary status and version.

The default threshold is one new review; PMs may choose an integer 1–100.
`pending_reviews` refresh flushes a partial batch. Guidance refreshes can publish
a new version without increasing review coverage. Pending reviews are in a unique
membership ledger; a durable marker on the saved review supports crash recovery.
Publication advances a fenced current pointer, leaving older versions immutable.
The worker resumes frozen review/guidance IDs and generation checkpoints on retry.

Every published version has an independent Hindsight retain outbox with a stable
`summary:<product>:v<version>` identity. Mongo publication remains visible if
Hindsight is unavailable; `memory_status` reports pending, synced, or failed and
the outbox retries with its saved operation checkpoint. Guidance passed to summary
generation comes from stored PM decisions; the UI does not claim Hindsight recall
was used when it was not. Keep pilot artifacts and sample manifests outside Git.

SIGINT/SIGTERM stop claims; lifespan cleanup closes model, memory and Mongo clients.
The worker rotates queues so a busy analysis queue cannot starve reviews/decisions.
`/health/live` reports process health; `/health/ready` checks Mongo capacity visibility,
not LLM connectivity. Neither endpoint exposes secrets. Interactive API contracts
are at `/docs` on the running local service. All application routes use `/api/v1`.

Demo bearer tokens are server-owned reviewer/PM identities, with no registration or
production account management. Keep this demo private; use an identity provider
before public deployment. Set `CORS_ORIGINS` explicitly (comma-separated origins).
Reviewers see their own submissions; PMs access product analysis and decisions.
There are no review edits/deletes because memory invalidation is not implemented.

## Example flow

Replace `PRODUCT_ID` with a loaded `parent_asin`. Retain the same idempotency key
when retrying an uncertain submission response. Reusing a key with changed input
returns 409. Review ratings are integers 1–5, titles at most 200 characters, and
nonblank text at most 10,000 characters; bodies are limited to 64 KiB.

```sh
curl -H "Authorization: Bearer $DEMO_REVIEWER_TOKEN" http://127.0.0.1:8000/api/v1/products
curl -X POST -H "Authorization: Bearer $DEMO_REVIEWER_TOKEN" -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-review-1' -d '{"title":"Stiff hinge","text":"The hinge feels stiff.","rating":3}' http://127.0.0.1:8000/api/v1/products/PRODUCT_ID/reviews
curl -H "Authorization: Bearer $DEMO_REVIEWER_TOKEN" http://127.0.0.1:8000/api/v1/reviews/REVIEW_ID/status
curl -X POST -H "Authorization: Bearer $DEMO_PM_TOKEN" -H 'Content-Type: application/json' -d '{"mode":"memory","scope":{"source":"user_submission"}}' http://127.0.0.1:8000/api/v1/products/PRODUCT_ID/analysis-runs
curl -H "Authorization: Bearer $DEMO_PM_TOKEN" http://127.0.0.1:8000/api/v1/analysis-runs/RUN_ID
curl -H "Authorization: Bearer $DEMO_PM_TOKEN" 'http://127.0.0.1:8000/api/v1/products/PRODUCT_ID/findings?run_id=RUN_ID'
curl -X POST -H "Authorization: Bearer $DEMO_PM_TOKEN" -H 'Content-Type: application/json' -d '{"kind":"correction","rationale":"Treat stiffness as a preference until failure evidence exists.","evidence_ids":["REVIEW_ID"]}' http://127.0.0.1:8000/api/v1/products/PRODUCT_ID/decisions
curl -X POST -H "Authorization: Bearer $DEMO_PM_TOKEN" -H 'Content-Type: application/json' -d '{"run_id":"RUN_ID","question":"What do reviews say about the hinge?"}' http://127.0.0.1:8000/api/v1/products/PRODUCT_ID/questions
```

Submission is durable before memory/classification finishes. Poll status; accepted
memory operation IDs are not completion. After correction, submit a later review
and request a new memory run. Older report content remains unchanged (its `stale`
flag may change). Baseline mode bypasses memory. Imported reviews lacking `source`
are `amazon_2023`. Scope supports `batch_id`, `available_through`, and explicit
`evaluation`; held-out Batch C is isolated and requires evaluation mode.

## Verification and interpretation

Run tests on a dedicated **local** MongoDB server. Fixtures create/drop random
`test_` databases and never derive their URI from application credentials. This
task used an existing isolated server on port 27031. To create your own server,
use a separate empty data directory, e.g. `mongod --port 27031 --bind_ip 127.0.0.1
--dbpath work/test-mongo` after creating that directory.

Exact verification commands (from `backend/` unless stated otherwise):

```sh
TEST_MONGODB_URI=mongodb://127.0.0.1:27031 .venv/bin/python -m pytest tests/integration/test_flow.py -q
TEST_MONGODB_URI=mongodb://127.0.0.1:27031 .venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q app
# From repository root, importer regression in its separate environment:
PYTHONPATH=data/scripts .venv/bin/python -m unittest discover -s data/scripts/tests -v
```

The full suite skips one opt-in real-provider smoke. With a Groq key in the
server environment and the account confirmed on Free, explicitly run:
`RUN_GROQ_FREE_SMOKE=1 GROQ_FREE_CONFIRMED=1 .venv/bin/python -m pytest tests/integration/test_live_provider.py -q`.
This makes one synthetic extraction and validates citation provenance; it never
selects an alternative provider/model. It does not load `.env` automatically.
The public API acceptance uses
real local Mongo and deterministic model/memory doubles, proving orchestration,
snapshot isolation and citation provenance, not live provider semantics.
`tests/fixtures/semantic_cases.json` is a hand-labeled synthetic set: all three
candidate quotes pass mechanical provenance, but two claims lack semantic support.
Those counts are fixture checks, not model-quality benchmark results.

## Durable worker boundary

`app.worker.Worker` accepts a `JobRepository` and a mapping from `reviews`,
`decisions`, or `analysis_runs` to synchronous handlers. `tick()` handles at most
one available record and returns whether it claimed work. The configured process registers all three service handlers. Call `stop()` on shutdown;
handlers must use bounded provider timeouts and honor context cancellation.

A handler receives `(record, context)`. It returns
`JobResult(completed=True, updates={...})` only after provider completion is
confirmed. A bare operation ID never completes a job. Save accepted operation
IDs using `context.checkpoint('memory', {...})` (also `classification` or
`analysis`). Return `JobResult()` to preserve the checkpoint and defer the job
for five seconds without consuming a failure attempt. Pending operations can be
polled on later ticks; adapters must define their own provider operation deadline.
The caller should sleep when `tick()` returns false rather than busy polling.

Claims, renewals, checkpoint writes and final publication use owner tokens and
unexpired leases. Renewal runs during synchronous handler work. Shutdown or lost
ownership prevents result publication; lease expiry allows another worker to
resume. Failures retry after 2, 4, 8 and 16 seconds by default, with the fifth
failure terminal (the general delay is capped at 300 seconds). Stored error codes
are sanitized; exception strings are never persisted or logged by the worker.
Result/checkpoint objects are capped at 64 KiB, and result publication cannot
rewrite review input or job scope. Existing v2 migration queue indexes cover all
three record collections; only records explicitly carrying pending/running
`processing` state are eligible, so imported historical reviews remain unchanged.

## Questions and capacity enforcement

PMs can `POST /api/v1/products/{product_id}/questions` with
`{"run_id":"…","question":"…"}`. The result is
`{"answer":"…","evidence":[{"review_id":"…","quote":"…"}],"insufficient_evidence":false}`.
Only completed runs for that product are accepted. Answers cite exact evidence
pairs supplied from the published run. No model-generated database queries run.
An unsupported question can return `insufficient_evidence: true` and empty evidence.
Citation validation establishes provenance, not semantic correctness; users should
inspect qualifications and negation in cited reviews.

Readiness and write preflight require full visibility of **all non-system MongoDB
databases**, matching the importer. The quota metric is `dataSize + indexSize`,
not compressed `storageSize`. Missing, invalid or unauthorized telemetry returns
503, as does exhausted capacity. The ceiling remains **400,000,000 bytes**.
`DATABASE_CAPACITY_BYTES` may lower this ceiling; `CAPACITY_WRITE_RESERVE_BYTES`
defaults to 1,000,000 bytes. New durable records and worker output growth also
reserve four times their proposed BSON size to allow for index/record overhead.
Preflight is not an atomic global reservation: concurrent or external writes can
still exceed a quota. Keep separate headroom and monitor database usage.

New reviews, throttle records, PM decisions, run snapshots, findings, reports,
worker checkpoints and completion payloads are guarded. Claims, lease renewal,
retry/defer and small status updates remain available so existing work can fail
or retry safely. Read-only question answers do not create durable records.

`CAPACITY_CHECKS_ENABLED` defaults to `true`. An explicit `false` is permitted
only for a localhost MongoDB URI and a database whose name starts with `test_`.
Production telemetry failures must be fixed, never bypassed. Test databases are
included in quota totals when running against the same MongoDB deployment.

Numeric Settings fields accept their uppercase environment names. New limits:
`REVIEW_SUBMISSION_LIMIT=10` (per identity per minute across API instances),
`MAX_QUESTION_CHARS=2000`, `MAX_QUESTION_CONTEXT_CHARS=2000`. All numeric limits
must be positive integers. The question text limit cannot exceed 2,000 characters.
Oversized question context is rejected explicitly rather than silently truncated.

## Recovery and operational limits

Provider failures use bounded retries and sanitized errors. Failed jobs require
operator review; there is intentionally no public reset endpoint. Preserve the
complete `processing.checkpoints` object and immutable review/decision/snapshot
contents during investigation. For an ambiguous retention timeout, inspect the
saved bank and operation ID against Hindsight, establish its terminal outcome,
then explicitly requeue only after reconciliation. Never clear checkpoints to
force another retain. See the provider contract for `memory_reconciliation_required`
and operation-age handling. Old staged output and isolated memory banks are not
automatically garbage-collected, so operators must budget their growth.

Limits default to 1,500 reviews/run, chunks of 5 reviews/3,000 text characters,
100 merged findings, provider timeout 60 seconds, lease 180 seconds, and five job
attempts. Enqueue/model budget errors request a narrower scope; no silent truncation.
Historical reports count distinct supporting review IDs and do not infer population
prevalence or causation. Imported data and live feedback remain separate scopes.

Historical analysis requires one `scope.batch_id`. Its effective cutoff defaults
to that batch's `end_at`; explicit later cutoffs are rejected with
`cutoff_after_batch_end`. The effective cutoff is persisted in the scope and used
for review membership, PM guidance eligibility, and stale detection. A newly
created PM decision keeps its real creation time and is not backdated into an old
batch. Live `user_submission` scopes retain their existing arrival-time behavior.

The additive migration preserves v1 runs and findings. Their run, findings, and
question readers return HTTP 409 `legacy_analysis_unsupported`, with instructions
to create a new analysis run for the same product and batch. Regenerate via
`POST /api/v1/products/{product_id}/analysis-runs` using the desired mode and an
explicit historical batch (or a live submission scope). The old result stays
unchanged; the backend does not invent v2 snapshots or publication tokens for it.

Incremental review checkpoints freeze up to 100 immutable decision IDs, then read
those exact decisions on retry. Rationales remain in their original documents,
so their combined UTF-8 size does not consume the 64 KiB checkpoint budget.
Existing embedded guidance checkpoints remain readable. Missing referenced
guidance fails explicitly as `guidance_snapshot_unavailable`; supported APIs never
edit or delete decisions. The model adapter validates its complete serialized
input against a 5,000-character budget plus a 6,000-byte UTF-8 message-content budget (including instructions/schema), without truncating guidance. Oversized
input exposes `model_input_too_large` through processing status. Analysis run
responses likewise expose safe allowlisted processing failures; arbitrary
exception or provider text is never returned.

Groq requests reserve at most 1,536 output tokens. The conservative full-message
byte budget leaves room for framing under the documented 8k Free TPM quota; shared
usage and actual account limits can still cause 429s. No input is silently truncated:
all chunked reviews remain in scope, oversized individual reviews/context fail
explicitly, and incomplete generated JSON is rejected. HTTP 429 becomes
`model_rate_limited`: questions return HTTP 429, processing exposes the same code,
and the worker waits at least 60 seconds between bounded attempts (five by default).
There is no immediate transport retry. Large scopes may exhaust the Free allowance;
use smaller scopes or retry later after the account quota resets.

Groq analysis extracts one chunk per worker tick and defers the next for 60 seconds
without consuming a failure attempt. Validated chunk results are immutable documents
linked by a lease-fenced checkpoint, so process restarts and 429s resume the completed
prefix without paying the quota cost again. Missing results or changed chunk boundaries
fail explicitly as `analysis_checkpoint_unavailable`; no partial report is published.
All selected reviews still contribute to final validated counts. Checkpoint result
documents, like other unpublished output, currently require operator storage budgeting.

## Five-review demo scope

Set `scope.sample_size` to `5` when creating an analysis to select the first five
matching reviews in `(timestamp, _id)` order, or all matching reviews if fewer
than five exist. Omit it (or use null) for the full selected group. The scope is
saved with the frozen input, and all counts, memory inputs and evidence come from
that sample. The frontend defaults to this demo option and labels the report;
existing runs retain their original scope. Five long reviews can still require
multiple model requests, and provider/memory latency can vary.

## Live browser updates

Authenticated SSE endpoints provide product summary snapshots and review-change
signals at `/api/v1/products/{id}/events`, submission snapshots at
`/api/v1/reviews/{id}/events`, and analysis snapshots at
`/api/v1/analysis-runs/{id}/events`. Authorization matches the corresponding read
API, including submission ownership. Each connection starts with a full snapshot
and receives only changed data plus idle heartbeats. The server checks Mongo-backed
state every two seconds; the browser no longer issues repeated polling requests.
Frontend streams use fetch with a Bearer header (never a token in a URL), disconnect
for inactive views, and reconnect with bounded backoff. Use the bounded graceful
shutdown option above so open streams do not delay service restarts indefinitely.

## Vercel API deployment

The repository root `index.py`, `requirements.txt`, and `vercel.json` deploy the
FastAPI API to Vercel. Set the MongoDB, demo bearer-token, Hindsight and provider
variables from the private `.env` in the Vercel project's environment settings;
never commit that file. Run `npx vercel deploy --prod` from the repository root.
The health probes are `/health/live` and `/health/ready`.

Vercel hosts the HTTP API only. Keep the local summary worker and the local
model/ngrok launcher running against the same MongoDB database to process queued
summary updates. Vercel functions do not run the persistent worker process.
SSE connections close after four minutes and reconnect with a fresh snapshot.
If the ngrok URL changes, update `LOCAL_MODEL_API_URL` in Vercel and redeploy.

Connect the GitHub repository in Vercel's project Git settings with production
branch `main` to deploy future pushes automatically. This requires a GitHub login
connection on the Vercel account. The frontend is a separate deployment and is
not included in this API project.
