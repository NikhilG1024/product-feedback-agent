# Incremental summary cutover — 2026-09-28

This report separates verified local behavior from application-database progress.
The earlier v2 migration preserved 300 products, 300,000 imported reviews, and
three batches; see [its verification record](migration-v2-report.json). No raw
review or legacy report replacement is part of summary cutover.

## Application database status

| Step | Status | Evidence or next check |
| --- | --- | --- |
| V3 dry run and additive migration | Applied and verified by the root operator on 2026-09-28 | The application database retained 300 products and 300,000 source reviews before the demo run. |
| API route after restart | Verified | A real product summary GET returned HTTP 200 with `uninitialized` and no published current version. |
| Original initial draft import | Verified | 306 drafts across 300 products with 6,000 admitted selected-review inputs: 293 pending, 13 rejected, and zero published at the import checkpoint. |
| Curated Ultra corrective pilot | Published after separate automated source audit | Product `B00EHFJGW2`, version 2, has a published current pointer and coverage of 20 historical reviews, zero new reviews. The application database now has 307 versions total, of which one is published/current. |
| Dedicated summary worker | Full worker rehearsal pending | Local UI auto-auth patch and end-to-end worker rehearsal remain open. |
| Hindsight summary-version sync | Verified for the published pilot | `ensure_retained` completed in 10.1 seconds; current `memory_status` was subsequently observed as `synced`. |
| All-product initialization | Not claimed | One of 300 products has a published current summary; the remaining products still require reviewed publication. |

## Local acceptance evidence

`backend/tests/integration/test_summary_acceptance.py` uses a disposable
`test_summary_acceptance_*` Mongo database and wholly synthetic reviews, model
output, and Hindsight responses. Its focused run passed on local Mongo port 27032.
It rehearses an initial draft hidden until attributed approval and publication;
a cached GET with zero model calls; a threshold-one review update; immutable
history; threshold-three buffering of two reviews followed by an idempotent
manual flush; a guidance-only version with unchanged coverage; and a failed
memory retain followed by retry without losing the published version.

The OpenRouter Ultra free adapter was also exercised by the root operator with
a synthetic old-summary-plus-one-new-review prompt. Its 35.2-second response
was structurally valid and retained exact old and new contradictory citations.
The raw 20-review Ultra initial pilot required automated evidence curation and
an independent source audit before version 2 was approved and published. This
does not establish unattended semantic quality for other products or quota.

The last complete backend suite reported **275 passed, 1 skipped in 130.43s**;
42 subsequent focused final-fix tests passed. These are local checks, separate
from the live application-database observations above.

## Cutover controls

Use the same private environment for API, legacy worker, and dedicated
`app.run_summary_worker`. Keep `OPENROUTER_API_KEY` server-side. The summary path
is pinned to `nvidia/nemotron-3-ultra-550b-a55b:free` and a zero-price ceiling;
legacy Groq remains a separate workflow. Read paths cannot generate. Six
initialization workers, when used, must process distinct products with the same
pinned local initial model. The selected sample is at most 20 eligible historical
reviews per product and excludes held-out Batch C. Verify exact source quotes,
semantic review attribution, and the published pointer before treating any
candidate as current.

Before broader publication, inspect application-database validator/index
inventory, `dataSize + indexSize` across user databases, capacity headroom below
400,000,000 bytes, and active legacy jobs that might compete for provider quota.
After the local UI auto-auth patch, rehearse the full dedicated-worker path:
inspect a real product's cached summary and history through an API/browser
refresh, then verify a saved new review reaches exactly one new version. Do not
put pilot artifacts, credentials, model weights, or customer data in Git.

## Live review rehearsal — 2026-09-28

A clearly labeled `[DEMO TEST] USB-C cable option` review was submitted through
the local auto-auth UI for B00EHFJGW2. The saved review eventually produced
version 3, with 20 historical samples + 1 new review and the exact quotation
“please offer a USB-C cable option for this portable drive”. The original
version 2 SHA-256 stayed unchanged. Hindsight subsequently reported synced.
The free provider returned invalid output on earlier attempts; a subsequent
response passed schema and evidence checks (3,803 completion tokens, reported
cost 0). This is a successful retry, not evidence of deterministic latency.
The PM page polls every five seconds and no longer shows the initialization
progress panel. The reviewer status no longer mislabels generic pending work
as waiting for a review threshold.

## Selected-product display and catalog

Non-rejected initial drafts are now exposed in a separate `initial_candidate`
field and displayed with their pending status. Published summaries remain the
only current/history records. The UI always prefers the published version.
The Mediabridge B0019EHU8G candidate was corrected against all 20 selected
sources, independently audited, and published as version 3; its memory synced.
Five products with no usable initial draft or current summary were reversibly
archived from `products` into `products_without_usable_summary_archive`, leaving
295 active products. Their review and summary records were not deleted.
Restore an archived product by inserting its archive `product` document back
into `products` after correcting its summary; retain the archive record.
