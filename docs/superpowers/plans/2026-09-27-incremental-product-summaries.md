# Incremental Product Summaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve persisted product summaries immediately, update them incrementally after configurable review thresholds, and retain evidence-backed version history.

**Architecture:** MongoDB is authoritative for current pointers, immutable versions and input membership. A fenced per-product worker compacts prior structured state with new reviews; dashboard reads never invoke inference. Six initialization clients share one local model server and publish through the same repository protocol.

**Tech Stack:** Existing Python 3.12–3.14/FastAPI/PyMongo/Pydantic and React/TypeScript/Vite; Hindsight; existing Groq incremental adapter; pinned local llama.cpp and Qwen3-4B-Instruct-2507 GGUF for initialization, conditional on pilot results.

**Spec:** `docs/superpowers/specs/2026-09-27-incremental-product-summaries-design.md` (approved by user).

## Global Constraints

- Default update threshold is 1 review; PM setting range is 1 to 100.
- Initialize from 20 random eligible reviews per product, or all if fewer; exclude held-out Batch C.
- Six workers process different products using one pinned model, not six competing models.
- No hosted inference charges; no provider fallback or arbitrary model substitution.
- Enforce 400,000,000-byte `dataSize + indexSize` global user-database ceiling.
- Keep raw reviews, existing reports and all published versions; do not silently truncate source text or discard history.
- GET summary/history/version endpoints do not enqueue work or call models.
- Keep memory sync and summary status independent. Never claim recalled guidance was used when recall failed.
- No production tests on application MongoDB; use disposable local `test_` databases.
- Repo is currently an unborn Git checkout with previously staged project files. Preserve that index and the remote history. Do not make an unrelated root commit or force-push. Record task verification/diffs until Git reconciliation is possible.

## Review Focus

- Review acknowledged just before a crash must eventually reach the summary exactly once (Tasks 2, 4).
- Partial publication must not expose a staged version or duplicate coverage (Task 2).
- Six initialization jobs must not overwrite a later incremental version (Tasks 2, 6).
- Long/multibyte reviews and contradictory reports must not disappear during compaction (Task 3).
- Switching products during an asynchronous response must not display another product's summary (Task 5).

## File boundaries and shared contracts

Create `backend/app/summaries/` with `contracts.py`, `repository.py`, `generation.py`, `service.py`, `worker.py`, `api.py`, and `initialization.py`; keep the legacy analysis modules intact. `contracts.py` owns all summary request/response types. Wire the subsystem in `app/main.py` and a separate `app/run_summary_worker.py` entry point so old analysis jobs cannot monopolize the summary queue.

`SummaryVersion`: product_id, version, parent_version, job_id, kind (`initial`/`reviews`/`guidance`), narrative, themes (stable ID, description, issue type, polarity, exact evidence pairs), coverage (historical_sample_count, new_review_count), delta_review_ids, manifest_ref, model_identity, prompt_version, guidance_references, created_at, published_at. No inferred prevalence numbers.

`SummaryView`: product_id, current (SummaryVersion or null), last_updated_at (null before publication), update_threshold, pending_review_count, status (`uninitialized`, `waiting`, `queued`, `updating`, `ready`, `failed`), error_code, memory_status. History returns `{items, next_cursor}`; detailed versions use the same schema.

`SummarySettingsInput`: strict integer update_threshold in [1,100]. `RefreshInput`: reason (`pending_reviews` or `guidance`); header Idempotency-Key required. `SummaryQuestionInput`: version positive integer plus bounded question. Reviewer processing response gains a separate `summary` field with status/version; existing processing fields remain compatible.

Published history is the current pointer's immutable parent chain. A staged version not reachable from that chain is never a public version, even if a crash leaves its document behind. Reconciliation repairs publication metadata and ledger entries using that chain. Each version's delta IDs are bounded; cumulative coverage numbers are deterministic.

## Task 1: Contracts, schema and additive migration

**Files:** create `backend/app/summaries/__init__.py`, `contracts.py`, `backend/app/migrations/v3.py`, `backend/tests/test_summary_contracts.py`, `backend/tests/integration/test_summary_migration.py`; modify `data/scripts/mongo_contract.py` only to add a separate v3 inventory.

**Interfaces:** Pydantic models above; `migrate(database, dry_run: bool) -> dict` in v3. Existing v2 schemas/importers continue to work.

- [ ] Write contract tests: `SummarySettingsInput(update_threshold=1)` and 100 accepted; 0, 101, True, floats and strings rejected; null current allowed only with no last_updated_at; unknown fields rejected. Run and confirm missing-contract failures.
- [ ] Define structured model output separately from app-owned metadata. Exact evidence pairs use review_id/quote; schema bounds narrative to 4,000 characters, 30 themes, 3 quotes per theme, 500 characters per quote. Excess output fails validation instead of being sliced.
- [ ] Write migration tests for dry-run no writes, repeat apply, preservation of all v2 documents/indexes, and refusal of incompatible validators/index options. Run and confirm failure before implementation.
- [ ] Implement three collection validators and unique product/version and product/review indexes. Add queue, pending input, version/job and history indexes. Use application-generated string IDs, strict/error validators, quota preflight, and v3 marker; no startup migration.
- [ ] Run `TEST_MONGODB_URI=mongodb://127.0.0.1:27032 .venv/bin/python -m pytest tests/test_summary_contracts.py tests/integration/test_summary_migration.py -q` from backend. Expect all pass. Record the diff/test result without disturbing previously staged files.

## Task 2: Durable input ledger and fenced publication

**Files:** create `backend/app/summaries/repository.py`, `backend/tests/integration/test_summary_repository.py`.

**Interfaces:** `SummaryRepository(database, capacity)` exposes `admit_review(review)->None`, `claim(product_id, now, lease_seconds)->SummaryClaim|None`, `renew(claim, now)->bool`, `freeze(claim, review_ids, guidance_ids)->FrozenUpdate`, `stage(claim, version)->str`, `publish(claim, version_id, now)->bool`, `reconcile(product_id)->None`, `current(product_id)->SummaryView`, `history(product_id,cursor,limit)->HistoryPage`, `version(product_id,number)->SummaryVersion|None`. Claims carry owner token, lease expiry, parent version and stable job_id.

- [ ] Write failing tests for unique admission; threshold counts unique pending inputs; frozen inputs exclude later arrivals; same timestamp ties use ID order; current read has zero provider calls.
- [ ] Implement indexed ledger and per-product compare-and-swap lease, monotonically allocated version IDs, immutable parent-linked version staging and current-pointer publication. Freeze at most 20 new reviews per job; threshold may accumulate up to 100 before processing starts, and follow-on jobs drain the eligible batch without dropping its remainder.
- [ ] Add crash-injection tests before/after staging, pointer publication and ledger reconciliation. Assert old current remains readable before publication, published inputs never contribute twice, history excludes orphans, and expired owners cannot publish.
- [ ] Implement reconciliation against published parent-chain deltas. A retry reuses job identity and frozen input; a duplicate initialization cannot publish when current already exists. Enforce capacity on growing documents; reads and recovery remain available at capacity.
- [ ] Run `TEST_MONGODB_URI=mongodb://127.0.0.1:27032 .venv/bin/python -m pytest tests/integration/test_summary_repository.py -q`; expect all pass and record results.

## Task 3: Evidence-grounded incremental generation

**Files:** create `backend/app/summaries/generation.py`, `backend/tests/test_summary_generation.py`; minimally extend `backend/app/integrations/llm.py` and `backend/app/config.py` for structured summary generation without relaxing the Groq allowlist.

**Interfaces:** `SummaryGenerator.generate(product, parent, reviews, guidance, checkpoint)->GeneratedSummary`. Provider interface `generate_summary(messages, output_schema)->dict`; app supplies coverage/model/manifest metadata. `GeneratedSummary` contains narrative/themes only plus validated evidence attribution.

- [ ] Write failing tests: prior narrative is never counted as new evidence; unsupported citation or wrong-product review fails; new contradictory feedback survives; zero-review guidance refresh preserves coverage; multibyte prompt measurement includes schema/instructions; no raw processing/provenance metadata is sent.
- [ ] Implement compact parent context plus only new review title/text/rating/ID and eligible PM guidance. Validate inherited quotes against bounded source lookups and new quotes against frozen inputs. Preserve stable theme identities or omit theme counts; never invent numeric prevalence.
- [ ] Split requests by actual provider budget and checkpoint intermediate structured outputs. No review is silently truncated. A single unsupported oversized source yields an explicit failure that preserves current version. Preserve existing Groq pacing; initialization local provider budgets are independent.
- [ ] Add tests for over-budget parent/guidance, malformed JSON, incomplete generations, prompt injection treated as data, and a model suggesting removal of conflicting evidence. Keep a bounded structured contradictions field rather than trusting prose alone.
- [ ] Run `.venv/bin/python -m pytest tests/test_summary_generation.py tests/test_llm.py -q`; expect all pass. Record validation limitations: exact quotes do not prove semantic support.

## Task 4: Submission integration, worker and public APIs

**Files:** create `backend/app/summaries/service.py`, `worker.py`, `api.py`, `backend/app/run_summary_worker.py`, `backend/tests/test_summary_api.py`, `backend/tests/integration/test_summary_worker.py`; modify `app/main.py`, `app/services/reviews.py`, `app/services/decisions.py`, `app/api/reviews.py`.

**Interfaces:** `SummaryService.get(product_id,principal)`, `history(product_id,principal,cursor,limit)`, `get_version(product_id,principal,version)`, `settings(product_id,principal,input)`, `refresh(product_id,principal,key,input)`, `question(product_id,principal,input)`; `SummaryWorker.tick()->bool` and `stop()->None`.

- [ ] Write failing route tests for PM auth, product isolation, settings validation, paginated history, missing initial summary, evidence-backed version questions, idempotent refresh replay/409, and GETs never calling generation.
- [ ] Implement the five approved routes plus `POST /products/{id}/summary/questions`. Bound questions to selected published-version evidence and return insufficient evidence when unsupported. Old questions/report endpoints remain intact.
- [ ] Add durable outstanding marker to new submitted reviews and a reconciliation sweep for save-before-ledger crash. Admit existing user submissions at cutover once; never admit unsampled imported reviews. Reviewer status derives independent summary progress and preserves memory processing status.
- [ ] Implement dedicated summary worker with lease heartbeat, frozen generation checkpoint, retry/backoff, quota guard and shutdown recovery. Default threshold 1; lower threshold rechecks pending work; explicit refresh processes partial batch. Guidance changes queue a coverage-neutral refresh and use recorded scoped recall status.
- [ ] Test threshold 1 and N, lowering settings, initialization races, Hindsight failures, duplicate user retries, shutdown recovery, no old-history scan in incremental generation, and three reviews arriving while one update is running.
- [ ] Run both new test files and legacy submission/decision/auth tests against disposable local Mongo. Expect unchanged legacy contracts plus new summary behavior; record the focused diff.

## Task 5: Cached-summary dashboard and reviewer status

**Files:** create `frontend/src/SummaryDashboard.tsx`, `SummaryHistory.tsx`, `SummarySettings.tsx`, `SummaryEvidence.tsx`, `frontend/tests/summary.test.tsx`; modify `src/types.ts`, `api.ts`, `demo.ts`, `App.tsx`, `Reviewer.tsx`, and targeted styles. Preserve `Dashboard.tsx` as legacy detail.

**Interfaces:** TypeScript mirrors Task 1; Api adds `summary`, `summaryHistory`, `summaryVersion`, `summarySettings`, `refreshSummary`, `summaryQuestion`. The initial task must not write these files.

- [ ] Write failing component tests: product selection performs only summary GET; cached content remains visible during updates/failures; initial empty state is explicit; switching product ignores obsolete requests.
- [ ] Implement default summary page with last-updated date, version, source coverage label, pending count, status and evidence. Show “Based on 20 sampled historical reviews + N new reviews” using actual sample count. No old run button is required to see current summary.
- [ ] Add PM threshold controls (1–100, default 1), update-now control with stable retry idempotency key, paginated historical versions, and questions bound to the selected version. Keep current and historical views visually distinct; do not show current guidance as if it existed in older versions.
- [ ] Update reviewer status: saved, awaiting threshold, updating, included/version, failed. Keep memory status separate. Update mock adapter with clearly illustrative immutable versions and deterministic threshold behavior.
- [ ] Add tests for invalid threshold, 403 roles, refresh response loss, history pagination, old-version evidence, fewer-than-20 source label and no full-dataset claims. Run `npm test` and `npm run build`; expect pass. Verify desktop/mobile in browser with fixture API and record results.

## Task 6: Local model pilot and six-worker initialization

**Files:** create `backend/app/summaries/initialization.py`, `backend/app/integrations/local_summary.py`, `backend/tests/test_summary_initialization.py`, `docs/summary-initialization.md`. Separate task owns model/runtime artifacts outside Git; share repository contract from Tasks 1–3.

**Interfaces:** `initialize_product(manifest, reviews, repository, generator)->InitializationResult`; CLI accepts manifest/review JSONL paths, workers=6, local provider URL, dry-run/pilot/publish modes. Uses Task 2 staging/publication, not direct pointer writes.

- [ ] Validate prepared manifests from `/Users/nikhilgollapalli/Documents/Codex/2026-09-27/product-summary-initialization/outputs`: 300 products/6,000 reviews, exact hashes, no held-out C, matching product, B-cutoff sample eligibility. Store manifest/sample time in initial version; exclude later corrections from seed prompts.
- [ ] Have the initialization task install/pin the proposed runtime and single weight artifact after plan approval. Verify source hash/license and six bounded slots on loopback; no credentials in logs or unverified downloaded code execution. Pilot one product, then six concurrent distinct products; record resource usage and actual quality/latency before full run.
- [ ] Write failing tests for duplicate product jobs, manifest mismatch, restart resume, fewer than 20 reviews, submitted-review arrival during initialization, model failure never publishing, and no repeated admission of unsampled old reviews.
- [ ] Implement adapter and six-client bounded scheduler sharing one local server. Keep raw candidate outputs outside public versions until schema/provenance checks pass. Stop and report OOM/critical pressure or sustained >1 GiB swap growth relative to baseline before reducing concurrency.
- [ ] Run `.venv/bin/python -m pytest tests/test_summary_initialization.py -q`; then real pilot. Exact citations must all validate; report human semantic-quality limits honestly. Only validated candidates publish. Full initialization resumes failed products without overwriting existing versions.

## Task 7: Cutover, rehearsal and documentation

**Files:** modify root `README.md`, `backend/README.md`, `frontend/README.md`, `.env.example`, `docs/backend-implementation-status.md`; create `docs/summary-cutover-report.md` and `backend/tests/integration/test_summary_acceptance.py`.

- [ ] Write failing end-to-end acceptance: initial sample → immediate summary read → save review at threshold 1 → new published version → old version unchanged → threshold 3 buffers two reviews → manual flush → guidance-only version preserves coverage.
- [ ] Run all backend tests on disposable Mongo and frontend tests/build. Review concurrency/capacity/citation boundaries with a fresh reviewer before live cutover. Fix discovered failures and repeat only affected checks.
- [ ] Snapshot application counts/validators/indexes, dry-run/apply v3, verify imported records untouched and quota headroom. Do not alter old running analysis scope; inventory legacy jobs and surface any that could compete for provider resources.
- [ ] Start API, dedicated summary worker and existing memory worker with the same private environment. Rehearse one real product before broader publication. Confirm product selection never invokes the model and history survives browser refresh.
- [ ] Initialize remaining products with six workers after the resource pilot passes. Report completed/pending/failed counts; do not claim full initialization until every expected product has a validated published version.
- [ ] Document exact local model launch, separate summary-worker command, threshold behavior, historical-sample coverage, Hindsight state, retry/recovery and remaining limitations. Preserve reproducibility manifests outside Git.
- [ ] Reconcile existing Git history and staged changes only after authentication works. Push only within the user's existing authorization, without force push or secrets/model weights/data; otherwise report the authentication blocker separately.

## Execution and dependency order

Tasks 1 → 2 → 3 → 4 establish the publication contract. Task 5 follows Task 4;
Task 6 may research/setup runtime in its existing task while implementation proceeds,
but publication waits for Tasks 2–4. Task 7 integrates everything. Preserve the
user's earlier subagent-driven execution preference for backend/UI implementation;
the separately requested initializer remains its own task.

## Self-review

Spec coverage includes all three collections, threshold and flush, independent
memory state, immutable history, immediate reads, six-way sampled initialization,
legacy preservation, UI and tests. Interface names above are consistent across
tasks. Remaining model quality/concurrency are measured gates, not assumptions.
Git history is an operational constraint, not grounds for inventing a new remote
history or committing already-staged unrelated changes.

## Addendum: user-requested live initialization progress

Applies to Tasks 4–6. Create `backend/app/summaries/progress.py` with
`InitializationProgressReader(configured_path).read()->InitializationProgress` and
a PM-only `GET /api/v1/summary-initialization/progress` endpoint. The separate
initializer writes versioned `generation-progress.json` via atomic rename, with
run ID, total and per-product states/timestamps. Confirm its actual schema before
writing the adapter. Accept only the server-configured path, cap file size, validate
schema/counts and sanitize errors. No client path argument or raw review text.
Mongo published state remains authoritative and is displayed separately from
generation and citation validation. Missing/malformed/stale progress cannot be
shown as zero remaining or completed.

- [ ] Task 4 tests missing/malformed file, atomic replacement, stale timestamp,
  inconsistent counts, PM authorization and generated-but-unpublished distinction.
- [ ] Task 5 adds overall initialization progress plus product status/last updated;
  bounded polling stops on unmount and ignores stale product responses. Test that
  validated drafts are never presented as published summaries.
- [ ] Task 6 coordinates the producer's schema and records an actual progress
  transition during the pilot; Task 7 verifies it in the connected UI.

### Confirmed initializer progress contract (schema v1)

Configured source: `/Users/nikhilgollapalli/Documents/Codex/2026-09-27/product-summary-initialization/outputs/generation-progress.json`.
Do not hardcode this machine-specific path into product code; use
`SUMMARY_INITIALIZATION_PROGRESS_PATH` in the private server environment.

Fields: `schema_version:1`, `run_id`, `started_at`, `updated_at` (aware UTC ISO8601),
`status` (`running`, `completed`, `completed_with_failures`), `total`, `workers`,
`completed`, `failed`, `active`, `queued`, `published:0`, optional `elapsed_seconds`,
and `products` keyed by product ID. Product records contain `title`, `status`
(`queued`, `generating`, `citation_checks_passed`, `reused`, `needs_review`) and
optional `elapsed_seconds`/`error`. Sanitize arbitrary error strings; expose only
allowlisted codes. `completed` means citation checks passed/reused, NOT published
or human semantic validation. Infer generated-success display from these states;
do not invent a separate unreported generated count. Derive publication separately
from MongoDB and match the manifest/run, never trust the file's `published` field.
Producer uses a single-writer lock and temp-file atomic rename. A smoke run may
have total=1; full initialization has total=300 and a new run ID. Use the actual
run total and clear old client progress on run ID change. Staleness should be
explicit after a configurable interval, not misreported as a job failure.
