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
  separate attributed semantic review; staged or rejected drafts remain hidden.
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
no claim about live-provider quality or the application database.

## Application cutover status

The application database received v2 earlier and v3 was applied after a dry run
on 2026-09-28. The API has been restarted and returned HTTP 200 with an explicit
`uninitialized` summary for a real product. Local initial draft import and
quality review are in progress; publication counts and memory sync completion
must be confirmed in the [cutover report](summary-cutover-report.md). The
dedicated summary worker should start only after approved initial versions are
published and the final review fixes are validated. No real product is described
as initialized here while its current pointer is empty.

## Runtime and remaining checks

Follow [backend setup](../backend/README.md) to load the same private environment
for the API, legacy worker, and dedicated summary worker. `OPENROUTER_API_KEY` is
server-only; `HINDSIGHT_API_URL` and `HINDSIGHT_API_KEY` control independent memory
sync. Do not run automated tests against the application database. The live
cutover still needs approved draft publication, worker startup, cached read and
history checks, memory-sync observation, and final product counts. Raw reviews,
legacy reports, and unpublished pilot artifacts are retained.
