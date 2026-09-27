# Product feedback frontend

The default PM page shows the currently published product summary from a cached
GET. It displays the publication date, version, exact cited review quotes,
historical sample and new-review coverage, pending count, summary status, and a
separate Hindsight memory status. Product selection never starts a model job.
Open **Version history** when needed to fetch older pages; questions and evidence
stay bound to the selected immutable version. **Legacy analysis** retains the
earlier run-based workflow.

## Run

With Node 22.12+ and npm:

```sh
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173. The app begins in clearly labeled **Sample mode**.
Its summaries and versions are illustrative local fixtures, including a four-review
historical sample. Sample mode sends no review or AI request to a server.

To connect to the real API, start the backend processes as described in
[backend setup](../backend/README.md), select **Connect API**, and enter the
server-configured PM or reviewer demo bearer token. The Vite proxy targets
`http://127.0.0.1:8000` by default; set `API_PROXY_TARGET` to change only that
address. Tokens live in browser memory and clear on reload or disconnect. Never
put OpenRouter, Groq, Hindsight, or Mongo credentials in `VITE_` variables or the
Connect API field.

For a local demo without entering tokens in the UI, run
`LOCAL_DEMO_AUTH=1 npm run dev`. Vite reads the two demo tokens from the root
`.env` and injects them server-side for the appropriate API routes. This mode
accepts only loopback clients and a loopback backend; credentials are never
bundled into browser code. The app connects automatically. Production builds
do not enable this development-only proxy mode.

## Summary and reviewer behavior

A product without a published version shows its non-rejected generated initial
candidate with an explicit validation/publication-pending label. Published
versions take priority. Only products without either show an empty state. The
coverage label uses the actual number of sampled historical reviews, up to 20,
plus incorporated new reviews; it makes no full-dataset claim. Existing current
content remains visible during a later update or failed attempt. The live page
checks the selected product every five seconds and displays new published versions
automatically. Initialization progress is not shown on the customer feedback page.

The default update threshold is one new review. PMs can set a whole number from
1 to 100 or choose **Update now** to flush pending reviews. A retry after an
uncertain refresh response reuses its idempotency key. History is requested on
demand and paginated. A historical version has distinct styling, its own evidence
and guidance references, and questions addressed to that version.

Reviewer status shows a saved review separately from summary inclusion and from
memory/classification processing. A review can be saved, await the threshold, be
queued/updating, and later appear as included in a specific summary version.
Hindsight failure does not erase a saved review or published Mongo summary.

## Verify

```sh
cd frontend
npm test
npm run build
```

For a browser fixture check without live providers, use the existing
`frontend/tests/contract_server.py` and `npm run test:contract` workflow. Do not
point integration tests at the application MongoDB database. The production
bundle is written to `dist/`; deploy it with an HTTPS reverse proxy for `/api/v1`.
