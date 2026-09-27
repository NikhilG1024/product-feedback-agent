# Product Feedback Intelligence: MongoDB data contract

## Verified initial baseline — 2026-09-27 (before expansion)

Live ingestion into `product_feedback` has stored 3 products, 547 real reviews and 3 chronological batches. The analysis_runs, findings and decisions collections are initialized and empty. All 11 indexes (6 automatic `_id_` + 5 secondary) exist. Full readback, references, chronology and repeat-import verification passed. Second import inserted zero documents. Both unhinted review queries use their intended IXSCAN indexes. Initial post-upload storage plus indexes was 524,288 bytes, well below the 400 MB ceiling. Authentication succeeded after the user supplied a corrected credential. The secret remains only in the ignored owner-only `.env` file locally.


The working tree had no local commits and contains existing untracked scaffold files. No commits, pushes, fetches or remote history changes were made. Local and GitHub histories are not synchronized.

The user subsequently requested a larger, broader dataset. See `docs/expanded-dataset.md` and live expansion reports for current counts; the baseline figures below are historical.

## Source and selection

Official source: [Amazon Reviews 2023, McAuley Lab](https://amazon-reviews-2023.github.io/). Download through the lab's [Hugging Face repository](https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023), pinned revision `2b6d039ed471f2ba5fd2acb718bf33b0a7e5598e`.

Files: `raw/review_categories/Electronics.jsonl` and `raw/meta_categories/meta_Electronics.jsonl`. An exploratory stream inspected the first 30,000 metadata rows, yielding 50 title/rating-based headphone candidates, then the first 4,000,000 review rows. The reproduction command directly retains the final selected three products. It never saves the whole Electronics category or sends category-scale data to MongoDB.

Selection: substantive text of at least 20 whitespace-separated words; metadata-confirmed headphone products; shared review coverage beginning 2021-07-01; repeated concern vocabulary in discovery Batch A. Earlier Kidz Gear reviews were excluded from the final product selection because their history ended before the newer products' coverage. No rating filter selects the final reviews. All qualifying records for the three products in the bounded slice and period are retained. The initial 500–1,500 target is met without oversampling or fabricated reviews.

| Product / parent_asin | A | B | C (held out) | Total |
|---|---:|---:|---:|---:|
| Panasonic ErgoFit / B07S764D9V | 46 | 21 | 26 | 93 |
| kurdene S8 / B0BHWYQ47Y | 111 | 66 | 29 | 206 |
| Beats Studio Buds / B0C338S8M7 | 116 | 50 | 82 | 248 |
| Total | 273 | 137 | 137 | 547 |

For example, low-rated Batch A reviews literally mention “sound” 10/19/33 times for Panasonic/kurdene/Beats, and “battery” 5/13 times for kurdene/Beats. These counts are screening signals, not issue classifications or confirmed defects; substring matching can include unrelated comparisons. No findings or evaluation answers were generated. Wired and wireless products remain separate; do not compare battery concerns across all products as one family-wide issue.

The stream prefix is a convenience sample, not random, not full product history, and not representative of Amazon purchasers. Length filtering favors longer reviews. Do not claim population complaint rates, representative trends, a verified root cause, or current product performance. Source parent groupings can span variants and have crawl-era titles newer than individual reviews; preserved variant `asin` enables finer analysis.

## Normalization and provenance

Inspected source records use `timestamp` (13-digit Unix milliseconds), `helpful_vote`, `rating`, `title`, `text`, `asin`, `parent_asin`, `verified_purchase`. Documentation examples alternatively use `sort_timestamp`/`helpful_votes`: both aliases are accepted; conflicting aliases fail. Integral Unix seconds are converted only within the historical range; milliseconds are range-checked through September 2023. JSONL uses ISO UTC strings plus original normalized milliseconds; MongoDB stores timestamps as BSON dates.

Product `_id` is the original `parent_asin`. Reviews preserve both parent and variant IDs, full title/text, rating, timestamp, available helpful/verification fields, source dataset/revision/path/1-based source line and a content hash. Images, videos and reviewer identifiers are omitted, including from the retained review cache. Product metadata keeps title/store/categories and a crawl-snapshot provenance note; aggregate ratings, prices, features and descriptions are excluded to avoid presenting future product state as earlier knowledge.

Review `_id` = `ar23:` + SHA-256 of canonical JSON containing parent_asin, asin, timestamp_ms, numeric rating, title and text. Content is not stripped/rewritten. This provides reviewer-free identity, with the limitation that two indistinguishable reviews from separate reviewers can merge. Exact content duplicates keep the first source line; same ID with differing non-provenance fields fails as a collision/conflicting duplicate. Source row offsets and the pinned upstream revision allow audit without retaining reviewer IDs.

## Collections and minimal schemas

Executable validators are in `data/scripts/mongo_contract.py`. All `_id` fields are strings. MongoDB JSON Schema checks document types and ranges; cross-collection references and temporal constraints are validated by the loader/application, not foreign-key constraints.

| Collection | Required fields beyond `_id` | Intended data |
|---|---|---|
| products | title, provenance | 3 canonical product identities; optional store/categories |
| reviews | parent_asin, asin, title, text, rating (1–5), timestamp (date), timestamp_ms, batch, batch_id, held_out, dataset_id, provenance | 547 source reviews; helpful_vote and verified_purchase may be null |
| batches | dataset_id, label (A/B/C), held_out, start_at/end_at (dates), review_count, product_counts | 3 immutable global batches |
| analysis_runs | parent_asin, batch_id, created_at, available_through (dates), mode, status | Initially empty; future application results |
| findings | analysis_run_id, parent_asin, issue_type, description, nonempty unique review_ids | Initially empty; cited issue evidence |
| decisions | parent_asin, decided_at, available_through (dates), kind, rationale | Initially empty; real human decisions only |

`mode`: baseline/memory; `status`: pending/running/completed/failed; `issue_type`: reported_defect/preference/feature_request/other. Application writes must verify finding review IDs belong to the run's product and allowed batch, and decision evidence does not exceed the allowed historical cutoff. No LLM extraction or Hindsight integration is implemented here.

## Created index inventory

| Collection | Index | Purpose |
|---|---|---|
| all six | unique `_id_` (automatic) | Product, review, batch and application record identity |
| reviews | parent_asin ASC, timestamp ASC | Product equality then chronological range/sort across batches |
| reviews | parent_asin ASC, batch_id ASC, timestamp ASC | Product + batch equality then chronological evidence retrieval |
| analysis_runs | parent_asin ASC, created_at DESC | Latest analysis history for a product |
| findings | analysis_run_id ASC, parent_asin ASC, issue_type ASC | Run-scoped product issue lookup; evidence IDs fetched through review `_id_` |
| decisions | parent_asin ASC, decided_at DESC | Latest product decision history |

Five secondary indexes plus six automatic identity indexes. No standalone parent_asin, batch, review_ids, text, vector or redundant unique index. Global batch metadata is retrieved by `_id`; membership is queried through reviews. The two review indexes serve different sorts: the intervening batch field cannot supply overall timestamp ordering when batch is not constrained. No partial indexes: required fields are present for every applicable document, and no relevant selective predicate justifies one. Uniqueness/collision checks precede insertion; no custom unique build is needed beyond `_id`.

Index order follows the [MongoDB compound-index guidance](https://www.mongodb.com/docs/manual/core/indexes/index-types/index-compound/). Validators follow [MongoDB schema validation](https://www.mongodb.com/docs/manual/core/schema-validation/). Existing collections must have the exact expected managed validator; the loader reports conflicts instead of silently imposing or replacing a contract.

## Reproduction and validation

Follow `data/scripts/README.md`. Local files total 640,360 JSONL bytes (about 0.64 MB); this is not a measured MongoDB storage figure. The user imposed a 400,000,000-byte total user-database ceiling, leaving at least 100 MB of the stated 500 MB capacity. The loader requires full non-system database visibility, sums uncompressed dataSize plus indexSize, and reserves the larger of 8 MB or four times incoming BSON size before any writes. Missing stats/permissions stop ingestion; they are not assumed to be zero. Post-import stats recheck the ceiling. System database storage is outside this application allowance; actual Atlas quota telemetry is authoritative. Concurrent unrelated writes can invalidate a preflight estimate. Live stats must include existing database contents and index overhead. The loader uses bounded documents far below the intended free-tier budget, but does not assume capacity of an existing database.

Local verification checks content IDs, duplicates, every product/batch reference, exact batch counts and boundaries, representation of every product in every batch, held-out flags and deterministic file hashes. Unit tests cover alias conflicts, units, collision failure, time ties, reference/content/count/holdout tampering and no-overwrite behavior. Batch C text is excluded from committed samples and docs. Live verification completed successfully; see data/processed/mongodb-validation.json.

## Attribution

McAuley Lab, Amazon Reviews 2023. Cite: Hou, Yupeng; Li, Jiacheng; He, Zhankui; Yan, An; Chen, Xiusi; McAuley, Julian (2024), *Bridging Language and Items for Retrieval and Recommendation*, [arXiv:2403.03952](https://arxiv.org/abs/2403.03952). Preserve dataset attribution and consult upstream terms before redistributing broader review data. Reviews remain source customer statements, not team findings.
