# Reproduce the MongoDB data layer

Run all commands from the repository root. Python 3.11+ recommended; locally exercised on 3.14.7.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r data/scripts/requirements.lock.txt
# Streams 30,000 metadata and 4,000,000 review source lines, retaining only selected candidates.
# Network transfer is much larger than the ~640 KB final subset; the whole category is never saved/imported.
.venv/bin/python data/scripts/extract.py --download
# With the prepared ignored raw cache already present:
.venv/bin/python data/scripts/extract.py
PYTHONPATH=data/scripts .venv/bin/python -m unittest discover -s data/scripts/tests -v
```

Create `.env` by copying `.env.example` only if `.env` does not already exist. Enter `MONGODB_URI` securely in your local editor and `MONGODB_DATABASE` with your intended application database. Do not paste credentials into chat or commit `.env`. Existing environment variables take precedence. No default database or automatic provisioning is used.

```bash
.venv/bin/python data/scripts/import_mongodb.py --preflight-only
.venv/bin/python data/scripts/import_mongodb.py
```

The loader checks all six target collections before writing. It also requires all non-system database storage/index statistics and enforces the user's 400 MB ceiling with an 8 MB minimum import reserve. It stops if full storage visibility is unavailable. Existing unmanaged/different validators, incompatible indexes, alternate natural identities, or changed records stop the import for manual review. It never drops collections/indexes, replaces records, changes existing validators, or edits network access. New collections use strict/error validation. Imported records are immutable: deterministic `_id` + `$setOnInsert` gives idempotence. Interrupted runs may leave a partial insert; rerun to finish safely. There is no cross-collection transaction, and concurrent writers should be paused during ingestion.

After the initial import, the loader reads all expected records back, checks references and chronology, repeats the import, and requires zero new inserts and unchanged counts. It writes `data/processed/mongodb-validation.json` with database name, counts, index inventory, representative IDs, `explain` plans (hinted and unhinted), and database storage stats where permitted. Small collections may legitimately favor collection scans; hinted plans demonstrate index support. The current run has created the schemas and indexes in the authorized `product_feedback` database; see the live validation report for completed checks.

`data/processed/local-validation.json` is local evidence only. The configured target is `product_feedback`. Authentication now succeeds; the live validation report is written only after all importer checks complete. Raw and processed directories and `.env` are gitignored. `data/samples` contains only six Batch A reviews and three product summaries.

See `docs/mongodb-data-layer.md` for schemas, selection rationale, attribution and index order, and `evaluation/README.md` for temporal isolation.


## Expanded 15-type dataset

The later user request targets 15 types × 20 products × 1,000 reviews. See `docs/expanded-dataset.md` for the expanded source selection, resumable scanner, staged import commands, preservation rules and quota checks. The baseline extractor/importer above remains a reproduction of the original 547-review snapshot; do not run it as an expansion reset or expect it to overwrite expanded batch metadata.
