# Product Feedback Agent

An evidence-backed product-feedback dashboard for product managers. The default
product page reads a cached, versioned summary with exact supporting quotes.
New submitted reviews update that summary when the product's threshold is met;
PMs can also request an update, browse immutable history, and inspect guidance.
The earlier run-based analysis remains available under **Legacy analysis**.

The project contains a React/TypeScript frontend, a FastAPI backend, a separate
background workers, MongoDB storage, Hindsight memory, and separate model adapters.
Incremental summaries use the exact OpenRouter Nemotron 3 Ultra free variant;
legacy analysis uses Groq. A saved review, published summary, and Hindsight sync
are separate states.

## Prerequisites

- Python 3.12–3.14 (tested with 3.14.7).
- Node.js 22.12+ and npm.
- MongoDB connection with collection/index management permissions and visibility
  into all user-database statistics. The application enforces a 400,000,000-byte
  ceiling for `dataSize + indexSize` across user databases.
- A server-side `OPENROUTER_API_KEY` for incremental summaries. The runtime pins
  `nvidia/nemotron-3-ultra-550b-a55b:free` with a zero-price provider ceiling.
- A server-side Groq key for optional legacy analysis.
- Hindsight API URL and key for independent summary-version memory sync and
  legacy memory mode.

## 1. Clone and install

```sh
git clone https://github.com/NikhilG1024/product-feedback-agent.git
cd product-feedback-agent
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.lock.txt
backend/.venv/bin/python -m pip install -e './backend[dev]'
# Used by the commands below to load the root .env safely.
backend/.venv/bin/python -m pip install 'python-dotenv[cli]'
cd frontend
npm ci
cd ..
```

## 2. Configure your local environment

Copy the template only if you do not already have a `.env`:

```sh
test -f .env || cp .env.example .env
chmod 600 .env
```

Edit `.env` locally and fill these settings:

| Variable | Purpose |
| --- | --- |
| `MONGODB_URI` | Your MongoDB connection URI |
| `MONGODB_DATABASE` | Target database name, e.g. `product_feedback` |
| `GROQ_API_KEY` | Model credential, used only by the backend |
| `OPENROUTER_API_KEY` | Server-only incremental summary credential for Nemotron 3 Ultra free |
| `HINDSIGHT_API_URL` | Your Hindsight service endpoint |
| `HINDSIGHT_API_KEY` | Hindsight service credential |
| `DEMO_PM_TOKEN` | Demo PM access token for analysis, questions and decisions |
| `DEMO_REVIEWER_TOKEN` | Different demo token for review submission/status |

Generate each demo token separately with `openssl rand -hex 32`. These tokens are
simple shared demo identities, not production user accounts. Enter a demo token
in the frontend's **Connect API** dialog; never enter a provider key there.
`LLM_API_KEY`, if populated, overrides `GROQ_API_KEY`, so leave it blank unless you
intend that override. Keep the Groq account on its Free plan; the application
cannot enforce account billing settings or guarantee available quota.

The backend does **not** automatically read `.env`. The `dotenv run` commands below
load it for each process. Restart the API and worker after changing configuration.
Keep secrets out of Git and all `VITE_` variables; frontend environment variables
are not a safe place for credentials.

## 3. Prepare MongoDB

If using the team's existing imported database, **do not reimport the dataset**.
For a new database, follow [dataset setup](data/scripts/README.md) and
[expanded dataset instructions](docs/expanded-dataset.md). Raw and processed data
are deliberately excluded from Git. Creating the application schema alone does
not populate the product catalog. The original extractor reproduces the original
547-review subset; the expansion procedure is separate.

Back up your target database, inspect the migration dry run, then apply:

```sh
cd backend
.venv/bin/python -m dotenv -f ../.env run -- .venv/bin/python -m app.migrations.v2 --dry-run
.venv/bin/python -m dotenv -f ../.env run -- .venv/bin/python -m app.migrations.v2
cd ..
```

The additive migration installs validators and indexes for imported and submitted
reviews, analysis jobs, decisions, idempotency and rate-limit expiry. It refuses
unknown incompatible schemas and does not rewrite imported reviews. The team's
application database already received v2; see the dated
[migration verification report](docs/migration-v2-report.json).

After backing up and reviewing the [summary cutover report](docs/summary-cutover-report.md),
run the separate v3 dry run and migration before enabling summary writes:

```sh
cd backend
.venv/bin/python -m dotenv -f ../.env run -- .venv/bin/python -m app.migrations.v3 --dry-run
.venv/bin/python -m dotenv -f ../.env run -- .venv/bin/python -m app.migrations.v3
cd ..
```

V3 adds summary state, immutable versions, and an input membership ledger. It does
not reimport or rewrite raw reviews. An uninitialized product returns an explicit
empty summary until a locally generated draft passes citation and semantic review
and is published. The local initial artifact path uses up to 20 eligible historical
reviews per product and excludes held-out Batch C; retain its sample manifest and
review record outside Git.

## 4. Start four processes

Run each block in a separate terminal, starting at the repository root.

**Terminal 1 — API**

```sh
cd backend
.venv/bin/python -m dotenv -f ../.env run -- .venv/bin/uvicorn app.main:configured_app --factory --host 127.0.0.1 --port 8000
```

**Terminal 2 — background worker**

```sh
cd backend
.venv/bin/python -m dotenv -f ../.env run -- .venv/bin/python -m app.run_worker
```

**Terminal 3 — dedicated summary worker**

```sh
cd backend
.venv/bin/python -m dotenv -f ../.env run -- .venv/bin/python -m app.run_summary_worker
```

**Terminal 4 — frontend**

```sh
cd frontend
LOCAL_DEMO_AUTH=1 npm run dev
```

Open [the app](http://127.0.0.1:5173). The Vite server proxies `/api` to the local
backend at port 8000. API documentation is at [Swagger UI](http://127.0.0.1:8000/docs).
Stop each process with Ctrl+C. For frontend-only exploration, run just terminal 4;
**Sample mode** uses illustrative fixtures and does not call AI services.

## 5. Use the cached summary

With local demo auth enabled, select a product without entering a token. Vite reads
`DEMO_PM_TOKEN` and `DEMO_REVIEWER_TOKEN` from the root `.env` on the server and
uses the appropriate role for each request. This mode is limited to localhost
and is not enabled in production builds. The current published summary
loads from MongoDB without a model call. Coverage says how many historical reviews
were sampled and how many new reviews have been incorporated; it is not a claim
about the full dataset. Open history on demand to inspect old versions and ask
questions tied to their evidence. Reviewer submissions show summary inclusion
separately from memory processing.

The default update threshold is **1** new review. A PM may set an integer from
1 to 100; reviews below that threshold remain pending. **Update now** flushes a
partial batch with a retry-safe idempotency key. A guidance change may create a
coverage-neutral version. Mongo publication survives a Hindsight outage; the
separate memory status records sync and retry. See [backend instructions](backend/README.md)
for the API and recovery details.

## 6. Run a legacy analysis demo

1. Choose **Connect API** and enter `DEMO_PM_TOKEN` from your local configuration.
2. Select a product and open **Find common issues**.
3. Keep **Analysis size → Demo: 5 reviews** selected. Choose an available review
   group and **Start fresh** for the initial baseline.
4. Start analysis. Demo scope freezes the first five matching reviews in
   chronological order (timestamp, then ID), or fewer if fewer exist. The dashboard
   labels the sample, and counts refer only to its reviews.
5. Open a finding to inspect its supporting quotes and source reviews.
6. Save relevant PM guidance. A later **Use previous guidance** run can use eligible
   recalled context; historical runs respect their cutoff, so a correction created
   today does not retroactively enter an old historical batch.
7. To demonstrate newly submitted reviews, use **Change account** with the reviewer
   token, submit a review, then switch back to the PM token and analyze **New app
   reviews**. Wait for memory processing before demonstrating a memory comparison.

**Full selected group** remains available, with a duration warning. Five reviews
is a sample limit, not a guaranteed single model request: long reviews can require
multiple chunks. Free-tier extraction is paced at 60 seconds between chunks;
provider rate limits and Hindsight processing can add delays. Existing large runs
keep their original scope; selecting demo mode requires a new run.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| API fails to start | Load `.env`; both demo tokens must be present and distinct. |
| 401 or 403 | Use the correct demo token. Reviewer access does not permit PM actions. |
| Empty catalog | Import a prepared dataset or connect to the populated team database. |
| Jobs remain pending | Confirm the separate worker is running with the same environment. |
| Readiness returns 503 | Check Mongo connectivity, database-stat permissions and capacity. |
| Model rate limit | Allow retries; use five-review scope and avoid concurrent large runs. |
| Model input too large | Use a smaller scope; unusually long review/context inputs still fail explicitly. |
| Guidance absent from a report | Check memory completion, product/source scope and historical cutoff. |
| UI cannot reach API | Confirm port 8000; override `API_PROXY_TARGET` when starting Vite if necessary. |

`/health/live` checks the API process. `/health/ready` checks Mongo and capacity;
it does not prove model or memory availability. Completed reports are persisted;
copy a run ID to reopen it after refresh. See [frontend instructions](frontend/README.md)
for session history and token handling.

## Tests

```sh
cd frontend
npm test
npm run build
cd ../backend
# Start a disposable local MongoDB server first. Never use the application DB here.
TEST_MONGODB_URI=mongodb://127.0.0.1:27017 .venv/bin/python -m pytest
```

Backend integration fixtures create and drop temporary `test_` databases. Live
provider smoke tests are opt-in; ordinary test doubles do not prove real-provider
quality. Data-pipeline tests and optional browser checks are documented in their
respective guides.

## Repository layout

- `frontend/` — dashboard, reviewer form and sample-mode adapter.
- `backend/` — API, model/memory integrations, worker, migration and tests.
- `data/scripts/` — extraction, import and data validation tools.
- `data/samples/` — small reference samples; full datasets are not committed.
- `docs/` — architecture, provider contracts and implementation notes.
- `evaluation/` — evaluation guidance and temporal isolation.
- `content/` — presentation/demo materials.

See [backend details](backend/README.md), [provider contract](docs/provider-contract.md)
and [dataset provenance](docs/mongodb-data-layer.md). This is a local hackathon
prototype; public deployment needs individual authentication and deployment setup.
