# Signal frontend

React + TypeScript implementation of the approved blue demo interface. Product summary and clickable issue chart are the primary screen; no bottom issue list. Review entry, exact evidence, team guidance, and analysis options appear only when needed. `mockups/` preserves the design prototype separately.

## Run

From this directory, with Node 22.12+ (verified with Node 26.8):

```sh
npm ci
npm run dev
```

Open http://127.0.0.1:5173. The app starts in **Sample mode**, clearly labeled, with no network calls or real AI claims. Click **Find common issues**, choose **Use previous guidance**, then **Start analysis**. Chart bars open review excerpts and the full source review. **Add guidance** saves a sample note; **Check progress** simulates its preparation. Run again to see it referenced. **Write a review** demonstrates saved-but-pending processing.

## Connect to the actual backend

1. Configure/start the API and worker using `../backend/README.md`. Backend configuration and migrations are owned by the backend task. Do not point integration tests at the application database.
2. The frontend proxies `/api` to `http://127.0.0.1:8000` by default. Override only the server address if needed:

   ```sh
   API_PROXY_TARGET=http://127.0.0.1:8002 npm run dev
   ```

3. Choose **Connect API** and enter a server-configured demo bearer token. The PM token permits analysis/guidance; the reviewer token permits review submission. **Change account** changes tokens. Navigation between views does not change server permissions.
4. Tokens are held in memory only, never written to browser storage, URLs, source files, or logs. Reloading/disconnecting clears them. Groq and Hindsight secrets remain exclusively on the backend. Never put secrets in `VITE_` variables.
5. Product pages come from the backend, 30 at a time, with **Load more products** and search of the loaded set. No live product IDs/categories are hardcoded. Review groups are discovered from the selected product’s paginated reviews because there is no batch-list route. The demo’s three products are not a catalog limit.
6. Choose historical Amazon reviews and one real group, or new app reviews. An optional UTC cutoff is available under **Choose an earlier date**. Group C is excluded. The backend enforces the 1,500-review maximum and reports when a narrower selection is required. Historical analysis requires a group and a cutoff no later than that group’s end; leave the optional date blank to use the backend’s group-end default.

The user selected **Groq's Free plan** after both direct and official local OpenCode probes failed. The Groq adapter is implemented, with `GROQ_API_KEY` stored only in the local server environment. Configured defaults are `https://api.groq.com/openai/v1` and `openai/gpt-oss-20b`, using JSON output and backend validation. Do not use retired legacy Llama model IDs. Stay on the Free plan; no billing upgrade or paid fallback is authorized. Account-specific limits in Groq Console are authoritative.

To proceed, create a key at https://console.groq.com/keys and save it as `GROQ_API_KEY` in the existing local project `.env` or backend process environment. Do not paste the key into chat, frontend code, or the browser's Connect API field. The browser takes only the configured PM/reviewer demo access token. Follow the backend task's final instructions to launch the API and worker. The backend does not automatically load `.env`: the variables must be loaded/exported into both server and worker processes as documented there. One live Groq extraction has passed: one synthetic chair review produced one finding with one locally validated verbatim citation. This is not a model-quality benchmark or a full real-service end-to-end test. The backend implementation and reviews are complete: 171 tests passed with 1 opt-in skip, plus 23 importer checks and 43 independent auth/model checks. The team application database has received the v2 migration; the API runs locally and has not been deployed. Sample mode remains available without credentials, and provider errors are never replaced with fabricated findings.

MongoDB, Hindsight, and server-configured PM/reviewer access tokens remain required for the connected workspace. The worker must run for pending jobs to finish.

## State and API behavior

- Every application route uses `/api/v1`. `src/types.ts` mirrors public product/review/analysis/decision models; `src/api.ts` is the HTTP adapter. `src/demo.ts` is a separate in-memory adapter.
- An analysis is polled until completion/failure; partial summaries/findings are not shown. Counts come directly from `supporting_review_counts`, not memory or frontend inference. Source/group/date/mode remain attached to the displayed snapshot. Stale reports are labeled.
- The LLM/Hindsight workflow belongs to the server. Memory-mode summary displays its returned `guidance_references`. Start-fresh mode reports that no prior guidance was used. No confidence scores or unsupported time trends are created.
- Reports opened during this session are available under **Analysis history**. Copy a run ID to reopen a persisted report after refresh; a run for another product is rejected. The backend has no run-list endpoint.
- Review retries reuse the same `Idempotency-Key` and exact payload. On a lost response, the form stays locked for retry. A definite rejection permits correcting the unsaved draft. Saved reviews are not editable.
- Guidance has no backend idempotency contract. An uncertain save disables a second POST and offers reconciliation against recent notes. If confirmation is ambiguous, ask the host to inspect before resubmitting. This avoids blindly creating duplicate memory entries.
- Memory preparation, classification, and durable save are separate states. A provider outage does not turn an acknowledged save into a failed submission.
- Evidence exposes exact source quotes and loads full matching reviews via paginated source/group filters. The UI states that quote provenance does not prove a technical cause.
- Questions are available in a collapsed **Ask about these reviews** section on completed reports. Request `{run_id, question}`; response `{answer, evidence, insufficient_evidence}`. Matches the backend QuestionInput/QuestionResponse models. Unavailable routes show an error rather than fabricated answers.

## Verify

```sh
npm test
npm run build
```

Browser smoke tests require installed Google Chrome and the running frontend:

```sh
npm run test:browser
```

Screenshots are written under `work/browser/` (ignored). The test covers pending-to-completed analysis, evidence, full review, saved guidance, later memory reference, product isolation, reviewer submission, and 390px mobile layout.

For a real FastAPI routing/serialization contract check **without real database or providers**, from the repository root:

```sh
PYTHONPATH=backend backend/.venv/bin/python frontend/tests/contract_server.py
```

In a second terminal, from frontend:

```sh
API_PROXY_TARGET=http://127.0.0.1:8001 npm run dev -- --port 5174
```

In a third terminal, from frontend:

```sh
npm run test:contract
```

The fixture binds only localhost:8001 and injects deterministic in-memory services into the actual backend app. Its `fixture-pm` and `fixture-reviewer` values are fake test tokens. It does not load `.env`, connect to MongoDB, or call Hindsight/LLM. It checks catalog pagination past 30 products, PM analysis/evidence/guidance/questions, reviewer submission/status, and server-enforced 403s. Stop the fixture and test Vite process afterward.

## Production bundle

`npm run build` writes `dist/`. A deployed host must serve static files and reverse-proxy `/api/v1` to the backend over HTTPS; Vite's dev proxy is not a production server. Public deployment and production identity integration are outside this task.

All implementation changes are inside `frontend/`. No shared Git history, backend, data, or secret files were changed.

### Final backend synchronization

The backend completed its implementation and reviews (171 tests passed, 1 opt-in skip; 23 importer checks; 43 independent auth/model checks). One live Groq extraction succeeded with a validated citation, not a full real-service end-to-end flow. Application cloud migration v2 is applied; public deployment is still outstanding. `.env` is not automatically loaded; export server/worker variables as documented by the backend. Groq analysis defaults to 5 reviews/3,000 characters per chunk with checkpointed resume and 60-second pacing per chunk, so historical runs may take several minutes. Oversized model inputs fail explicitly rather than being truncated. Legacy reports return a request to generate a new analysis.
