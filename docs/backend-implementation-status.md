# Backend implementation status — 2026-09-28

The backend now has two separate AI paths. The default product dashboard reads
cached, immutable MongoDB summaries. New review increments use the exact
OpenRouter `nvidia/nemotron-3-ultra-550b-a55b:free` model with a zero-price
provider ceiling. The earlier run-based analysis retains its Groq adapter and
worker. Neither path silently switches models.

## Delivered and verified locally

- V3 summary state, version, and input-ledger collections with an explicit
  additive migration and 400,000,000-byte user-database capacity guard.
- Locally prepared initial artifacts from up to 20 eligible historical reviews
  per product, held-out Batch C excluded. Source and citation checks precede a
  separate attributed semantic review. Non-rejected initial drafts can be previewed
  before publication; rejected drafts remain hidden.
- PM-only cached summary, paginated history, version, settings, refresh, and
  version-bound question routes. GET routes do not call generation.
- Durable saved-review admission, default threshold 1 (PM range 1–100), frozen
  worker inputs, lease fencing, generation checkpoints, immutable publication,
  and retry-safe manual flush. Legacy review processing stays independent.
- Separate per-version Hindsight retention outbox with a stable version ID and
  durable operation checkpoint. Mongo publication survives a memory outage.

The synthetic disposable-Mongo cutover acceptance test covers initial approval,
cached GET, one-review increment, immutable history, threshold 3 buffering and
flush, coverage-neutral guidance refresh, and Hindsight failure/retry. It makes
no claim about unattended live-provider quality. The last full backend suite
reported **275 passed, 1 skipped in 130.43s**, followed by **42 passing focused
final-fix tests**.

## Application cutover status

The application database received v2 earlier and v3 was applied and verified
after a dry run on 2026-09-28. Before the demo run, 300 products and 300,000
source reviews remained unchanged. The initial import admitted 6,000 selected
review inputs and staged 306 drafts across 300 products: 293 pending, 13
rejected, and zero published at that checkpoint. A separate curated Ultra
corrective pilot for `B00EHFJGW2` passed an independent automated source audit
and was published as version 2, with 20 historical reviews and zero new reviews.
That checkpoint contained 307 versions total and one published current pointer.
Its version-scoped Hindsight retention completed in 10.1 seconds, and its
`memory_status` was observed as `synced`. The raw 20-review model output needed
curation; this publication does not establish unattended quality for the
remaining drafts. See the [cutover report](summary-cutover-report.md).

## Runtime and remaining checks

Follow [backend setup](../backend/README.md) to load the same private environment
for the API, legacy worker, and dedicated summary worker. `OPENROUTER_API_KEY` is
server-only; `HINDSIGHT_API_URL` and `HINDSIGHT_API_KEY` control independent memory
sync. Do not run automated tests against the application database. The local UI auto-auth flow has been exercised without entering tokens. A labeled
USB-C demo review on B00EHFJGW2 was saved through the UI and incorporated into
version 3 by the real Ultra worker after invalid-response retries. Its exact new
citation and 20 historical + 1 new review coverage were checked in MongoDB;
version 2 retained the same SHA-256, and version 3 Hindsight status reached synced.
The dedicated worker is running locally. Broader cutover still needs semantic
review and publication of the remaining initial drafts. Raw reviews, legacy
reports, and unpublished pilot artifacts are retained.

## Catalog and initial preview

The current-summary response includes `initial_candidate` when no current version
is published. The UI labels it as a generated initial summary awaiting validation
or publication; it cannot enter published history or version questions.
On user request, five products having only rejected summaries were moved from
`products` into `products_without_usable_summary_archive`, retaining the complete
product documents and all reviews. The active catalog now has 295 products.
Mediabridge B0019EHU8G received an independently audited corrected initial
summary as version 3; new app reviews are queued for its incremental update.
