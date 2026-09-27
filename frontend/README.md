# Product feedback frontend

The default PM page shows the currently published product summary from the initial SSE snapshot. It displays the publication date, version,
historical sample and new-review coverage, pending count, summary status, and a
separate Hindsight memory status. Product selection never starts a model job.
Open **Version history** when needed to fetch older pages. The question form is
not shown on the dashboard. Summary evidence is retained in
the data but its separate UI section is hidden. **Legacy analysis** retains the
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
`https://product-feedback-agent-api.vercel.app` by default; set `API_PROXY_TARGET` to change only that
address. Tokens live in browser memory and clear on reload or disconnect. Never
put OpenRouter, Groq, Hindsight, or Mongo credentials in `VITE_` variables or the
Connect API field.

For a local demo without entering tokens in the UI, run
`LOCAL_DEMO_AUTH=1 npm run dev`. Vite reads the two demo tokens from the root
`.env` and injects them server-side for the appropriate API routes. This mode
accepts only loopback clients and either the exact approved Vercel URL or the local backend on port 8000; credentials are never
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

## Live review list

“What reviewers said” reads reviews directly from MongoDB, including submitted
reviews not yet incorporated into a summary. New app submissions appear first,
with negative, neutral, then positive ratings within each source group; newest
reviews lead within each group. Use Newest first for global time order.
Filter by star rating and rating-based sentiment (negative 1–2, neutral 3,
positive 4–5); filters intersect. The list polls every five seconds in live mode
and supports Load more. It shows five reviews initially and adds five per Load more click. It is hidden
when inspecting a historical summary so
current reviews cannot be mistaken for historical evidence.

## Startup readiness

Before the live workspace loads products, it checks `/health/ready`. A slow cold
start or transient failure is retried up to three times (12 seconds per attempt,
with 1- and 2-second delays). If readiness still fails, the page offers Retry.
Writes are never automatically retried. REST and SSE share the same backend.
Local Vite proxies `/api` and `/health` to Vercel. Production browser builds
default to the same Vercel origin, overridable with `VITE_API_ORIGIN`; a hosted
UI requires its origin in backend CORS settings and an appropriate auth flow.
Never place secret tokens in a `VITE_` variable.

## Product catalog and photos

The product picker shows individual product cards, category filters, and the
selected product's details. Product photos are matched by ASIN to original
Amazon Reviews 2023 product metadata, never by generic product subtype.
`public/product-images.json` maps product IDs to local image assets and records
source URLs. An unavailable image uses an explicit fallback. Images load lazily;
the catalog retains explicit pagination instead of downloading every review.
The desktop sidebar stays visible while the product content scrolls.

## Firebase public demo

Run `npm run build` in `frontend/`, then `npx firebase-tools deploy --only hosting`
from the repository root. The configured Firebase project is `pfia-nikhilg1024`.
Production builds call the Vercel API directly, check readiness before startup,
and request a temporary guest session; no localhost proxy or shared bearer token
is included in the deployed frontend. The token is kept in page memory and
expires after eight hours (reload to start a new session). Each new session has
its own reviewer identity. Guests can browse products and summaries and submit
reviews; administrative updates and legacy analysis are unavailable.

The backend requires `PUBLIC_DEMO_ENABLED=true` and the Firebase site's exact
origins in `CORS_ORIGINS`. This is intentionally an open hackathon demo, not
private account access. The local model/ngrok and summary worker still process
queued summary updates against the shared MongoDB database.
