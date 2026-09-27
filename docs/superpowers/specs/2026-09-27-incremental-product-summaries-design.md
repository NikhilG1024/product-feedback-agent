# Incremental product summaries — architecture redesign

Status: approved by user on 2026-09-27. This replaces the PM dashboard's on-demand bulk
analysis flow. Existing reviews and completed analysis runs remain readable.

## Intended behavior

Selecting a product reads its latest published summary from MongoDB; it never
triggers model inference. Before initialization finishes, show an explicit
“Initial summary being prepared” state, not a fabricated or empty success.

A submitted review is saved durably and acknowledged immediately. A background
summary worker incorporates new reviews into the product's existing structured
summary. Default update threshold is 1 review. The PM can configure a per-product
threshold from 1 to 100 and request an immediate update for a partial batch.
Thresholds count unincorporated reviews, not API requests or retries. Lowering a
threshold reevaluates pending work. Show pending count and whether the summary is
waiting for its threshold, queued, updating or failed.

Updates compact prior structured state plus the new review batch; they do not
query and resummarize every historical review. Keep immutable summary versions,
last successful update time, coverage, source IDs, model and prompt version.
A failure leaves the last completed version available.

## Data and provenance

Use three new collections:

- `product_summary_state`: one document per product; current published version,
  next version counter, settings, job lease/fencing token, selected input batch,
  retry state and initialization status. Derive pending inputs from indexed review
  membership; do not use an unbounded embedded array of all reviews.
- `product_summary_versions`: immutable versions, unique `(product_id, version)`;
  parent version, compact narrative and structured themes, evidence references,
  exact incremental review IDs, coverage counts, model/prompt identity,
  created/published timestamps, and initialization sample manifest reference.
- `product_summary_inputs`: unique `(product_id, review_id)` membership ledger;
  source, admission sequence/time and incorporating version. Supports idempotent
  submission replay, recovery and pending-count queries.

Versions store deltas plus parent links rather than repeating an ever-growing
review-ID list. History API is paginated and individual versions are addressable.
Initial sampling manifests preserve candidate population, random seed, selected
IDs, eligibility rules and sample time. Initialization pilot artifacts are stored separately from published versions;
parallel workers process distinct products and never multiply review coverage.

Counts are calculated by application code from review membership and validated
extractions, not inferred from generated prose. Theme counts are only exposed
when stable theme attribution is supported; unsupported counts are omitted.
Exact supporting quotes must resolve to included source reviews. Old summary
text is context, never fresh evidence. Contradictory new feedback must remain
visible instead of being silently overwritten by compaction. Historical versions
remain immutable, including after PM corrections.

## Durable update algorithm

1. An idempotent review write supplies an outstanding summary-input marker.
   Reconciliation discovers any saved review missed by a crash before ledger
   insertion. Hindsight synchronization and summary processing have independent
   status; one outage must not erase an acknowledged review.
2. Worker checks initialization and threshold, then claims a per-product lease.
   Freeze parent version, bounded unincorporated review IDs and applicable PM
   guidance. New arrivals wait for the next update.
3. Generate structured incremental state from the frozen parent plus new reviews.
   Bound requests using actual serialized prompt size; split batches if needed,
   checkpoint sub-results and compact without silently truncating review text.
4. Validate shape, coverage and evidence. Stage an immutable version with a stable
   job identifier. Compare-and-swap the product's current pointer under the lease
   and expected parent version. Only pointed-to versions are published.
5. Reconcile incorporated ledger entries against the published version. Recovery
   must not double-count inputs or let an expired worker overwrite newer output.
   Retries reuse frozen inputs. Do not require a cross-collection transaction
   without confirming the deployment supports it.
6. Continue with pending inputs if the threshold is met. Below threshold, wait
   for another review or a PM-requested flush. Version history remains available.

Retain the current Mongo capacity guard across versions, ledgers and indexes.
Capacity exhaustion stops new growth with a visible status; no silent history
deletion. Indexes cover product/version history, pending product inputs, unique
input membership and the worker queue. Migration is additive and explicitly run.

## Hindsight and PM decisions

Keep Hindsight for applicable PM corrections/preferences, separate from the
canonical Mongo summary. New review memory retention uses durable checkpoints.
Incremental summary generation recalls scoped guidance when available and records
which guidance affected that version. A guidance update can enqueue a refresh
without falsely increasing review coverage. Memory failure is surfaced; never
claim a version used guidance if recall did not succeed. Product/source and
held-out evaluation isolation remain enforced.

## Initial summaries and six parallel product workers

User clarification: divide products across six workers; do not compare six
models for every product. Select one suitable Hugging Face summarization model
and use the same pinned model/prompt across workers for consistent output.

For each product, select 20 random eligible imported reviews without replacement;
use all eligible reviews when fewer exist. Persist a seed and manifest. Exclude
held-out Batch C by default. The dashboard explicitly labels initialization as
a 20-review random sample; unselected old reviews do not automatically enter the
incremental queue. New submitted reviews arriving during initialization are
queued for incorporation once the initial version publishes.

A separate user-requested task researches model quality, licenses/access, pinned
revisions, Apple Silicon compatibility and six-way product processing. The local
machine has 36 GiB RAM. Zero hosted inference charges are required. Prefer shared
model serving when supported to avoid six redundant weight copies. Product jobs
have independent leases, checkpoints and resumable output; six workers must never
publish two initial versions of the same product.

Measure memory and actual throughput before the full initialization. Report a
resource constraint before lowering the requested six-way concurrency. Hugging
Face model availability does not imply unlimited free hosted inference. Select
using a small representative pilot assessing evidence validity, coverage,
unsupported claims, schema success, latency and memory; do not claim a universally
best model. The initialization model does not automatically replace the existing
incremental runtime: record provider/model explicitly and validate suitability
for both tasks. No model weights or API credentials enter Git.

## API and UI

- `GET /api/v1/products/{id}/summary`: latest published summary and pending/status
  metadata; no model call. Include version, last_updated_at, coverage and sampled
  origin, model and guidance references.
- `GET /api/v1/products/{id}/summary/history`: cursor-paginated published history.
- `GET /api/v1/products/{id}/summary/versions/{version}`: immutable detailed view.
- `PATCH /api/v1/products/{id}/summary/settings`: PM-only threshold configuration.
- `POST /api/v1/products/{id}/summary/refresh`: PM-only idempotent queue/flush;
  returns asynchronous state, not a synchronous generated response.

PM dashboard opens on cached summary with evidence and last-updated time. Show
“Based on 20 sampled historical reviews + N new reviews” rather than implying
full-dataset coverage. Settings offers “Update after every review” (default) or a
review-count threshold. History opens on demand with version dates and changes.
Questions reference the selected summary version and its evidence. Existing
analysis-run history is available as legacy detail; it is not the default page.
Reviewer UI distinguishes saved, awaiting threshold, updating, included, and
failed states. No per-review full-summary spinner blocks submission.

## Rollout and verification

Keep old data and current report endpoints through migration. Initialize summaries
in resumable jobs. Switch dashboard reads after new APIs exist; no reinterpretation
of old report records as summaries. Update mocks, API contracts and setup docs.

Tests must cover: threshold 1 and N, fewer than 20 seed reviews, deterministic
sampling, held-out exclusion, duplicate submission, arrivals during generation,
worker crash at each publication boundary, stale lease fencing, retries without
double counting, PM guidance refresh without additional review counts, version
history pagination, provider failure preserving the previous summary, capacity
limits, and zero inference on product selection. Test fixture summaries cannot
be presented as real model output. Run a real small-product rehearsal separately
from large initialization and record model/runtime evidence.

## Additional user request: initialization progress

During initial generation, display overall total/queued/active/generated/validated/failed
and published counts, per-product status and last progress update time. Initializer
produces atomic local progress and candidate files; generated or citation-validated
outputs are not equivalent to published Mongo summaries. Backend merges safe
initializer telemetry with authoritative Mongo publication state, exposes no local
paths or review text in progress, and reports stale/missing telemetry explicitly.
The client cannot select a filesystem path.
