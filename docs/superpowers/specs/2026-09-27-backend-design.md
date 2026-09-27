# Product Feedback Agent backend design

Status: ready for user review. Implements the backend plan discussed in this task.

## Outcome and scope

A reviewer can submit feedback for an existing product. A product manager can analyze scoped feedback, inspect cited evidence, ask questions, and record corrections or decisions that influence future analysis through Hindsight. MongoDB remains the authoritative record; Hindsight supplies contextual memory, not counts or durable job state. React is outside this backend change.

Use Python/FastAPI, MongoDB, a separate polling worker, Hindsight, and a configurable runtime LLM adapter. OpenCode is a development tool, not the application's model API. No model training is required. Runtime provider credentials and model names come from environment configuration.

Keep the hackathon implementation small: one API process, one worker process, one database. A synchronous request doing all model work is simpler but makes submissions fragile; a separate Redis queue adds unnecessary infrastructure. Use MongoDB durable jobs embedded in submitted reviews, decisions, and analysis runs.

## Existing data compatibility

Reuse products, reviews, batches, analysis_runs, findings, decisions and existing string IDs. Product identity is `parent_asin`, referring to products._id. Preserve imported review timestamp, timestamp_ms, asin, provenance, dataset_id, batch_id and held_out. Existing imported reviews have no source field; interpret those as amazon_2023. Do not rewrite imported text, IDs or chronology.

The current review validator requires dataset batch fields for every review. Introduce a versioned, explicit migration accepting either the existing imported-review shape or a user-submission shape. Submitted reviews require source=user_submission, parent_asin, asin, title, text, rating, timestamp, timestamp_ms, created_at, author_id, version=1, provenance and processing state. They have no fabricated A/B/C membership. User submissions cannot claim verified_purchase. Derive timestamp and author identity server-side.

Extend run schemas to accept an explicit scope object in place of mandatory batch_id for live-only runs. Preserve existing historical run fields and enum values. Store schema version in a migration record. Migration verifies the known old validator, checks existing documents against the proposed contract and applies additive changes only; unknown validators stop with a diagnostic. Coordinate the importer contract before changing shared files: the loader currently rejects validators it does not recognize. No destructive collection operations.

## Roles and configuration

Provide server-enforced reviewer and PM permissions. For this local hackathon backend, use configured opaque bearer tokens mapped server-side to stable user IDs and roles; no client-controlled role or author headers. This is demo authentication, documented as such. Public self-registration, account recovery and production identity integration are out of scope. Missing auth configuration fails closed; production deployment needs an identity provider before opening registration.

Configuration includes Mongo URI/database, separate reviewer/PM tokens, Hindsight endpoint/key, runtime LLM endpoint/key/model, CORS origin allowlist and worker limits. Never log secrets, raw auth headers or connection strings. Provide a secret-free example environment file. Defaults impose request size, text length, page size, run-size and model timeout limits. Enforce bounded submission throttling using persisted per-identity counters so multiple API processes cannot bypass it.

## HTTP contract

All application routes use /api/v1; health routes expose no secrets.

- GET /products and GET /products/{id}: authenticated product browsing, bounded pagination.
- POST /products/{id}/reviews: reviewer submission; require Idempotency-Key; validate existing product, integer rating 1–5, title up to 200 characters, substantive nonblank text up to 10,000 characters. Return 201 with review ID and processing state. Same user/key and identical payload returns the original result; changed payload returns 409.
- GET /reviews/{id}/status: owner or PM only.
- GET /products/{id}/reviews: PM evidence view; reviewers can retrieve their own submissions only. Cursor pagination and explicit source/batch filters; omit author identifiers from PM responses unless needed for ownership checks internally.
- POST /products/{id}/analysis-runs: PM only, return 202 with run ID. Body specifies baseline or memory mode and source scope: one historical batch or live submissions. Optional historical cutoff cannot exceed the chosen batch boundary. Default excludes held-out Batch C; an explicit evaluation flag enables it in a separate memory scope.
- GET /analysis-runs/{id}: PM only; status, scope, coverage, brief and failures.
- GET /products/{id}/findings: PM only; filter by completed run, include evidence IDs and exact quotes.
- POST /products/{id}/questions: PM only; bounded grounded answer over one completed run, with citations or an explicit insufficient-evidence answer. Never execute arbitrary model-generated database queries.
- POST /products/{id}/decisions and GET /products/{id}/decisions: PM only; correction, preference or decision, rationale, optional evidence IDs and applicability cutoff. Return saved record plus memory-sync state.

Use stable JSON error codes and standard 401/403/404/409/422/429/503 responses. Avoid exposing third-party error bodies.

## Durable processing

Insert a review and its pending processing intent in one document. Return as soon as Mongo acknowledges it. A worker atomically claims eligible work with a lease, owner token and expiry. State changes require the active owner token; expired workers cannot overwrite a newer worker's result. Use bounded retries, exponential backoff and terminal failed state. A restart recovers expired leases.

For a review: retain scoped, attributed memory; confirm actual completion; classify the new review using eligible PM guidance; validate output; save the classification on the review; mark processing complete. Classification is provisional, not a completed aggregate report. No reanalysis of all history per submission. Expose memory and classification status independently.

For a decision: persist exact user-authored content and pending memory intent together; retain in the appropriate scope; expose sync completion. A failed memory service does not lose a review or decision.

For an analysis run: save the immutable selected review IDs and a created-at cutoff before model calls, process bounded chunks, validate extracted findings, calculate counts in Python/MongoDB and persist findings under deterministic run-local IDs. Readers only consume results from completed runs. Retries upsert deterministic output IDs to avoid duplicate findings. An interrupted write leaves the run incomplete and recoverable.

## Hindsight boundaries

Hide SDK calls behind a MemoryStore interface. Verify the installed official SDK's retain/recall, completion polling, metadata filtering and document replacement semantics before implementation; pin the tested version. Do not assume accepting an asynchronous request means retention is finished.

Use separate product and source/evaluation scopes. Historical replay must never recall future live submissions or decisions. Filter decisions by applicability and availability cutoff. If reliable temporal filtering is unavailable, use separate banks per product/replay stage with only eligible records. Baseline mode bypasses memory completely.

Retained content identifies review/decision ID, version, source and date. Treat review claims as untrusted customer reports and PM decisions as guidance, never as verified defect fixes. Stable document IDs alone are not proof of remote idempotency; implement and test provider-supported replacement or reconciliation for timeout-after-success cases. Keep raw exact evidence in MongoDB. Editing/deleting reviews is excluded until memory invalidation is implemented.

Historical imported records are processed only within explicitly selected run scope, with retained-record checkpoints to avoid re-retaining the same eligible content. New user reviews enter live memory through their submission jobs. Batch C remains isolated from normal analysis memory.

## Analysis and evidence

The model receives product identity, eligible review text with IDs, and separately labeled recalled context. Review text cannot grant tool permissions or override instructions. The LLM adapter returns structured findings: issue_type, description, evidence review IDs and exact supporting quotes. Allowed issue types match the existing contract: reported_defect, preference, feature_request, other.

Reject unknown IDs, IDs outside the run product/scope, invented quotes, invalid enums and malformed output. Exact-quote validation proves provenance, not semantic truth; evaluate semantic support separately on a small labeled fixture. Never invent a confidence score or claim a confirmed root cause. Counts and denominators are deterministic, count each review once per theme, and state the analyzed sample size. Bounded chunk processing covers the selected run; if a configured maximum is exceeded, require a narrower scope instead of silently truncating.

Generate briefs with recurring issues, evidence, counts, suggested investigation and explicit limitations. Trends require comparable explicit time windows; a single batch has no fabricated trend. Historical Amazon samples and current app feedback are displayed separately. New reviews mark older results stale; requesting another run creates a new result instead of rewriting past analysis.

## Indexes and capacity

Preserve existing product/time, product/batch/time, run-history, findings and decision-history indexes. Add a unique partial index on submitted-review author_id plus idempotency_key (only user_submission documents), and indexes supporting processing status/next_attempt_at/lease expiry. Apply corresponding queue indexes for decisions and runs. Confirm query plans and avoid redundant product-only indexes. Existing automatic _id uniqueness remains authoritative.

Keep the existing 400,000,000-byte database capacity ceiling visible in operational documentation. Backend payload/run limits and bounded outputs constrain growth; report storage health and reject new work when an explicitly configured capacity check indicates the ceiling has been reached. Do not load more Amazon data as part of backend implementation.

## Module boundaries

backend/app/config.py: validated environment settings.
backend/app/main.py and api/: app lifecycle, routes, request/response schemas, auth.
backend/app/repositories/: Mongo queries, atomic claims, idempotency and snapshot persistence.
backend/app/services/: submission, decisions, analysis orchestration and grounded questions.
backend/app/integrations/: Hindsight and runtime LLM adapters.
backend/app/worker.py: job polling, leases, retry scheduling and shutdown.
backend/app/migrations/: compatible validators/indexes and migration CLI.
backend/tests/: behavioral unit, API and opt-in integration tests.
backend/README.md: setup, migration, API/worker commands, examples and operational limits.

## Verification and acceptance

Use Superpowers TDD: write and run failing behavior tests before implementation, then make them pass. Use fake memory/model adapters for deterministic failure scenarios; run real Mongo integration tests against a dedicated test database, not the imported application database. Live external-service tests are opt-in and report skipped credentials honestly.

Required tests: reviewer vs PM authorization; validation; concurrent idempotent submissions; idempotency payload conflict; imported-schema compatibility; atomic job claims; expired lease recovery and stale-owner rejection; retries after remote failures; retention pending vs completed; duplicate remote outcome reconciliation; product/time/holdout isolation; baseline memory bypass; citation rejection; deterministic counts; partial-run invisibility; PM correction used in later eligible analysis; historical reports unchanged; questions with insufficient evidence.

Acceptance demo: submit a review, observe its Mongo record and memory state, complete an analysis with valid citations, record a PM correction, submit another relevant review, and show a subsequent memory-enabled run using that correction. Baseline and memory runs use the same review snapshot for a fair comparison. Report which checks ran against real services versus test doubles.

## Delivery boundaries

Backend code, tests, explicit migration, secret-free configuration examples and run instructions are included. Frontend changes, public deployment, review edits/deletes, production login, automatic issue publishing and remote Git history changes are excluded. Preserve the existing uncommitted scaffold and data task files. The local repository has no committed base and has not fetched GitHub history; reconcile that before any push rather than creating an unrelated history.
