# Incremental summary cutover — 2026-09-28

This report separates verified local behavior from application-database progress.
The earlier v2 migration preserved 300 products, 300,000 imported reviews, and
three batches; see [its verification record](migration-v2-report.json). No raw
review or legacy report replacement is part of summary cutover.

## Application database status

| Step | Status | Evidence or next check |
| --- | --- | --- |
| V3 dry run and additive migration | Applied by the root operator on 2026-09-28 | Confirm exact collection validators, indexes, and headroom in the operator's snapshot. |
| API route after restart | Verified | A real product summary GET returned HTTP 200 with `uninitialized` and no published current version. |
| Local initial candidates | Import/quality review in progress | Keep raw pilot output, frozen source sample, manifest, model identity, and semantic review record outside Git. |
| Initial publications | Pending verification | Count approved current pointers separately from staged/rejected drafts. |
| Dedicated summary worker | Not started at this report update | Start after reviewed initial publications and final fixes. |
| Hindsight summary-version sync | Pending live verification | Observe version-scoped outbox statuses after publication; an outage does not revoke Mongo publication. |
| All-product initialization | Not claimed | Report approved, pending, rejected, failed, and uninitialized product counts before completion claim. |

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
a synthetic old-summary-plus-new-review prompt; the response retained exact
old and new contradictory citations. That request establishes provider
connectivity and contract behavior, not broad semantic quality or quota.

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

Before broader publication, record application-database validator/index
inventory, per-collection counts, `dataSize + indexSize` across user databases,
capacity headroom below 400,000,000 bytes, and active legacy jobs that might
compete for external provider quota. After starting the dedicated worker, inspect
one real product's cached summary and history through an API/browser refresh;
then verify a saved new review reaches exactly one new version and the independent
Hindsight outbox reaches a terminal state or a visible retry state. Do not put
pilot artifacts, credentials, model weights, or customer data in Git.
