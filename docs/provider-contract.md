# Provider contracts

## Incremental summary provider: authenticated local Qwen

Incremental summaries default to the pinned local llama-server alias
`qwen3-4b-instruct-2507-local`. The server-only `LOCAL_MODEL_API_URL` points
to `https://YOUR_NGROK_DOMAIN.ngrok-free.app/v1` when the ngrok model tunnel is active;
`http://127.0.0.1:4300/v1` is allowed for local development. Only the model
API is tunneled. `LOCAL_MODEL_API_KEY` supplies the llama-server Bearer token
and never reaches the browser. The adapter sends
`ngrok-skip-browser-warning: true` for ngrok free domains. Remote URLs must
use an HTTPS ngrok domain and have no embedded credentials, query, or fragment.

The request supplies the strict compact summary JSON schema to llama-server as
`response_format: {"type":"json_object","schema":...}`, matching the
[llama.cpp grammar format](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)
and the prior local initialization client. The schema includes the 900-character
narrative limit. Temperature is zero, output is bounded to 6,144 tokens, and
serialized prompt bytes are capped at 32 KiB. The local model returns only themes grounded in the new review batch. The generator
merges those themes into the immutable published parent in code, retaining old
theme IDs, evidence, contradictions, and opposite-polarity reports. New quotes
are checked as exact source substrings and every new review must be cited before
the merge. Three fresh quotes that fill a theme are split into a stable new
theme so an old representative quote is not discarded. The model rewrites a
compact narrative but does not rewrite prior theme provenance. A length finish reason fails as `model_output_truncated`; malformed
JSON or schema violations fail as `model_invalid_output`. Cached GETs make no
model call.

`SUMMARY_LLM_PROVIDER=local` is the default. Explicit `groq` selection uses
the separate pinned Groq Free summary client; it is never a fallback from a
local failure. Legacy analysis continues to use Groq separately. The backend
does not expose the model tunnel URL or key to the frontend.

This integration has deterministic mock-transport tests; the suite makes no
live model call. The public ngrok tunnel requires the operator to authenticate
ngrok and bind the configured domain before remote calls can succeed.

## Hindsight contract and SDK inspection

Installed SDK signatures inspected: `Hindsight(base_url, api_key, timeout=300,
max_attempts=3)`, `retain(..., document_id, retain_async=False, operation_id=None)`,
and `retain_batch(..., retain_async=False, operation_id=None)`. Generated
`RetainRequest` has `operation_id`; `RetainResponse` has `success`, `bank_id`,
`items_count`, `async`, and optional `operation_id`/`operation_ids`.
The implementation deliberately uses SDK models over a streaming HTTP transport:
this avoids the SDK's default retry behavior and bounds response bytes before JSON
parsing. There are no automatic transport retries or redirects.

The official [retain API reference](https://hindsight.vectorize.io/api-reference)
documents a client-supplied UUID as async operation identity. Repeating it returns
the original operation without creating new work; incompatible reuse returns 409.
The adapter sends one item with a stable UUID derived from bank, record ID, and a
canonical SHA-256 fingerprint of the entire `MemoryRecord`.

The [operations documentation](https://hindsight.vectorize.io/developer/api/operations)
describes `pending`, `processing`, `completed`, `failed`, and `cancelled`.
The adapter requires a validated `completed` operation before returning success.
It polls `/v1/default/banks/{bank}/operations/{operation}`. An accepted async ID
alone is never completion. Terminal failures remain failures on restart.

The [document API](https://hindsight.vectorize.io/developer/api/documents)
returns original text and memory count. Re-retaining the same document ID replaces
its old content/facts. Replacement is not assumed to be safe duplicate suppression.
Document existence alone is not a completion signal; a timeout never triggers a
second retain POST. The application must keep record IDs and contents immutable.

The [recall API](https://hindsight.vectorize.io/developer/api/recall) returns facts
with source `document_id`. `recall` requests world/experience facts, excludes
consolidated observations lacking direct document provenance, and returns
`MemoryContext(text, record_ids)` with unique source document IDs, not fact IDs.
It rejects missing provenance and oversized results. Recall is context, never
standalone evidence or proof of historical eligibility.

## Worker wiring and restart behavior

```python
state = record.get("processing", {}).get("checkpoints", {}).get("memory")
memory.ensure_retained(
    bank_id,
    memory_record,
    state=state,
    checkpoint=lambda value: context.checkpoint("memory", value),
)
# Only after this returns may a handler report confirmed memory completion.
```

The callback is mandatory even on a first write and must synchronously persist
through the worker's lease-fenced update. If it raises, submission stops. All
provider checkpoints are small JSON objects: `bank_id`, `record_id`, `fingerprint`,
`operation_id`, `started_at` (UTC Unix seconds), and `status`. Restore the exact
checkpoint after retry or takeover. Never reconstruct an empty state to retry an
uncertain submission. If a handler retains several records, persist a checkpoint
per record in a bounded stage object and pass only the matching entry to this
adapter; the adapter does not write MongoDB itself.

- `RetentionPending` (`memory_pending`): accepted operation is still pending after
  the call budget. Handler should return `JobResult()` to defer, preserving the
  checkpoint. Next invocation only polls the saved operation.
- `ReconciliationRequired` (`memory_reconciliation_required`): lost acknowledgment,
  missing operation, unknown status, or operation older than 900 seconds by default.
  Never treat this as success. A retry with the same checkpoint only polls, never
  submits. Let the bounded worker retry policy eventually make it terminal; do not
  defer forever. An operator must inspect the saved operation/document in the
  exact bank, establish the remote outcome, then repair/requeue explicitly.
- `memory_checkpoint_conflict`: bank, record identity, or content changed relative
  to the saved checkpoint. No remote write is made. Resolve the immutable-record
  conflict rather than discarding state.
- `memory_failed`: remote failed/cancelled status. No resubmission is attempted.
- Other `ProviderError` values are sanitized constants. Worker retries retain the
  checkpoint. Original provider exception bodies, credentials, and review text
  are not propagated through exception messages.

A completed checkpoint is durable evidence from an earlier confirmed poll. It
can return immediately on restart. Older servers returning their own operation ID
are supported by persisting the acknowledged ID; if that acknowledgment is lost,
the unknown write requires reconciliation. This conservative behavior does not
rely on every deployment supporting the current server's idempotency feature.

Default transport inactivity timeout and per-call deadline: 60 seconds. Polling
uses the remaining deadline; checks also run while reading chunks. A blocked read
can finish up to its bounded socket timeout after the deadline, so the worker
heartbeat remains required. Requests/responses are capped at 262,144 bytes; recall
text at 20,000 characters and 100 facts; query at 2,000 characters (provider may
reject its stricter 500-token limit). No unbounded retry loop exists. Call `close()`
at shutdown. Providers do not choose bank scope: tasks 5/6 must select isolated
banks, freeze historical eligibility, keep Batch C separate, and bypass memory
entirely in baseline mode.

## Groq Free model contract

The only allowed runtime selection is `openai/gpt-oss-20b` at
`https://api.groq.com/openai/v1`. Settings and the adapter enforce this exact pair,
with no alternate endpoint, model fallback, or automatic billing upgrade.
**The operator must keep the account on Groq Free.** Model/endpoint validation
cannot establish account billing status or prevent charges on an upgraded account.
`GROQ_API_KEY` is required for calls; an explicit `LLM_API_KEY` takes priority and
must also be a Groq credential. Legacy OpenCode/DeepSeek keys are ignored.
`GroqModel` is available; `DeepSeekModel` remains an import-compatible legacy name.
Missing credentials disable the configured application's model; direct adapter
construction without credentials fails before networking. Hindsight uses its own key.

Official documentation checked 2026-09-27:
[rate limits](https://console.groq.com/docs/rate-limits) lists Free gpt-oss-20b limits
of 30 RPM, 1,000 RPD, 8,000 TPM and 200,000 TPD; the account console is authoritative.
[Structured outputs](https://console.groq.com/docs/structured-outputs) documents
JSON object mode support. [Deprecations](https://console.groq.com/docs/deprecations)
lists the retired Llama free/developer options; they are not fallbacks here.

An authorized single synthetic extraction on 2026-09-27 succeeded using the user's
server-side Groq key: one finding and one exact locally validated citation. No
real review data, paid fallback, account changes or billing upgrades were used.
Previous OpenCode attempts were unavailable/denied; that runtime is superseded.

Requests use OpenAI-compatible chat completions, JSON instructions,
`response_format={"type":"json_object"}`, and at most 1,536 output tokens. Strict
local Pydantic validation rejects malformed output and incomplete finish reasons.
Full system/user message content (including instructions/schema) is bounded to
6,000 UTF-8 bytes, conservatively leaving room for framing and output under the
8k Free TPM limit. This is not a remaining-quota or billing guarantee. HTTP 429
is exposed as `model_rate_limited`; transport never immediately retries. Worker
retries wait at least 60 seconds and stop after the configured attempt limit
(default five). Questions return HTTP 429 so callers can retry later.

Analysis extraction persists each validated chunk in an immutable result document,
then links its compact identifier through a lease-fenced checkpoint. A retry reloads
and revalidates that completed prefix rather than reissuing successful model calls.
Groq processes one extraction chunk per worker tick, deferring the next for 60 seconds
without consuming failure attempts. Normal large scopes therefore make progress
across minute windows. Shared account usage/daily quota can still fail after bounded
429 retries. Missing checkpoint documents or changed chunk boundaries fail explicitly
with `analysis_checkpoint_unavailable`; no partial report is published. Immutable
chunk documents use the existing analysis output collection and capacity checks.
At the maximum 1,500 one-review chunks, compact checkpoint references remain below
the 64 KiB checkpoint bound. Stale/unlinked documents cannot overwrite linked data;
they consume storage until operator cleanup (no automatic garbage collection).

`extract(product, reviews, context)` returns `list[FindingDraft]`. The envelope is
`{"findings":[...]}`; only `reported_defect`, `preference`, `feature_request`, and
`other` are accepted. Strings, list lengths, nonempty evidence, and extra keys are
validated. The adapter retains a hard limit of 20 reviews and 40,000 review-text characters,
with a stricter 5,000-character total serialized data ceiling and the full-message
byte budget above. Default analysis chunks are 5 reviews/3,000 text characters;
question context defaults to 2,000 characters. Every chunk stays in the analysis
scope; oversized reviews or recalled context fail explicitly. Oversize input is rejected,
never silently truncated. Context and review text are explicitly untrusted data.
The analysis service validates citation existence, product/scope eligibility and
exact quotes; a separate hand-labeled evaluation illustrates semantic support; structured output alone proves none
of these.

`answer(question, findings, evidence)` returns:

```json
{"answer":"...","evidence":[{"review_id":"...","quote":"..."}],"insufficient_evidence":false}
```

Evidence input is a list of `review_id`/`quote` dictionaries. A substantive answer
requires citations; every returned pair must exactly match supplied evidence.
Insufficient answers may have empty evidence. The question service supplies only evidence
from an eligible completed run and validates its response contract. The adapter
cannot prove that a verbatim quote supports the answer's meaning.

## Validation performed

Deterministic HTTP-boundary tests cover retention progress, timeout/restart without
a second POST, terminal failure, unknown operations, lease callback failure,
checkpoint/content conflicts, response limits, recall provenance, malformed JSON,
unsupported categories, output structure, input budget rejection, and grounded
answer citation membership. Tests use actual SDK/Pydantic validation and HTTPX
MockTransport; they are not live model-quality measurements.

Live Hindsight smoke used only bank
`task4-synthetic-fbfece8b365e4e90ab86d00e08ca82fd` with one synthetic record. Retention
completed; the requested operation ID was preserved; original document content
matched and memory count was 1. Immediate recall returned an empty list, so live
semantic recall is inconclusive. The bank was deleted and confirmed absent from
the bank listing. A profile GET can return a default profile after deletion and
was not used as final proof of existence. No existing bank or MongoDB data changed.

Live free-model probes are described above; model quality remains unverified. There was no live ambiguous-write or concurrent duplicate fault
injection; those recovery claims are based on deterministic tests and the official
operation contract.

Package provenance: [Hindsight Python client 0.10.1](https://pypi.org/project/hindsight-client/0.10.1/).

## Analysis snapshot wiring (Task 5)

`AnalysisService.handle(record, context)` is registered as `analysis_runs` in the
configured worker. `python -m app.run_worker` runs the separate polling process.
The Groq model adapter requires a server-side Groq key; missing credentials leave
model operations disabled. Memory mode still
requires configured Hindsight; missing memory returns `provider_not_configured`.
Free-model unavailability produces bounded worker failures rather than invented output.

Each analysis gets an isolated `analysis-{run_id}` memory bank, including live
snapshots and evaluation runs. Snapshot review/decision documents are immutable
copies persisted before the run becomes claimable. Only the snapshot's product,
source, batch, availability cutoff, and eligible PM decisions enter this bank.
Decision eligibility requires server `decided_at` and `available_through` at or
before the cutoff, and `created_at` too when present. Referenced evidence IDs must
all belong to the snapshot; empty evidence guidance is permitted.

Analysis uses a bounded `processing.checkpoints.memory` envelope:
`{"next_index": 20, "current": null}`. The immutable ordered records are reviews
followed by decisions. The index is a durable certificate of the confirmed
completed prefix. While a record is in progress, `current` holds its exact adapter
checkpoint. Every adapter callback and every completed-prefix advance checks the
active lease and raises on a rejected write. A tick processes at most 20 retention
records; restart restores the exact current operation rather than resubmitting it.
This avoids accumulating 1,500 operation objects inside the worker's 64 KiB limit.

Findings and briefs are staged under the attempt's owner token. The worker's
lease-fenced completion publishes only that token after both outputs persist.
Readers require `status=completed` and select that published attempt. Public
finding `id` is UUID5(run ID, normalized theme), stable across retries; Mongo `_id`
adds the attempt token and is not a public finding identifier. Stale attempts
cannot overwrite or expose a winning attempt's output. Unpublished staged records
can remain after interruption; automated garbage collection is not implemented.

A theme's count is the exact number of distinct validated supporting review IDs.
NFKC/case/whitespace-normalized theme keys merge across chunks; conflicting model
categories become `other`. Up to 20 sorted verbatim quotes are displayed per theme,
with `evidence_sampled` disclosing any sampling. The full supporting review-ID set
and count remain available. Exact quotation proves provenance only; semantic
support is explicitly `model_interpretation`, not a guarantee about meaning.
No trend is calculated. Briefs include sample limitations and investigations,
not causal fixes. Recalled eligible decision rationales appear in the memory-mode
summary with `guidance_references`; baseline bypasses memory completely.

Report publication has a separate global finding bound: `MAX_ANALYSIS_FINDINGS`
(default100, configurable1–1000). After merging all chunks, exceeding this bound
records lease-fenced `analysis_output_limit_exceeded` and aborts before staging or
publishing output. The run response exposes that `error_code` while the existing
bounded worker retries proceed and after terminal failure. The PM can select a
narrower scope. The findings reader no longer silently truncates the published
set; every published chart-count ID is retrievable. Character-limited chunking can
produce more than75 chunks, so the bound applies to the final merged report rather
than relying on a presumed chunk count.

## Decisions and incremental review processing (Task 6)

Configured HTTP/worker applications now register reviews and decisions alongside
analysis runs. Saving feedback/guidance succeeds independently of provider health.
PM decision kinds are correction, preference, decision; server creation time is
never replaced by a requested historical applicability date.

Incremental classification uses an isolated review-{id} bank seeded only with the
review and guidance eligible at its server arrival time. Decision snapshots are
persisted before provider writes; later PM knowledge cannot enter that review's
retry. Standalone decision retention uses decision-{id}; historical analysis keeps
its original isolated analysis-{run_id} bank and eligibility contract.

Provider checkpoints remain exact and lease fenced. Confirmed memory completion
updates memory_status independently before recall/extraction; a later model failure
cannot erase the synced state. Terminal classification failures are explicit in
review status; provisional findings publish only in fenced completion. Retained
records contain product/source/type attribution, with no author identifiers.
