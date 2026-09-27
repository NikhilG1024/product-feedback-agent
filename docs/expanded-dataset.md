# Expanded Electronics dataset

## Verified completion

Live import completed on 27 September 2026: 300 products, 300,000 reviews, 15 types with 20 products each and 1,000 reviews per product. Full readback passed and the second pass inserted zero records. Final quota after lease release is 261,591,740 bytes (261.59 MB); 238.41 MB remains from the stated 500 MB allowance. All three batches are complete and the lease is released. The 23 local data tests passed. Evidence: `data/processed/expansion300/mongodb-validation.json`.

## Requested scope

The user superseded the initial 3-product/547-review baseline with 15 product types, 20 distinct products per type and 1,000 source reviews per product: target **300 products / 300,000 reviews**. The storage ceiling remains **400,000,000 bytes**, preserving at least 100 MB of the stated 500 MB allowance. This is a ceiling, not a storage target. Source discovery/import progress is recorded in ignored JSON files; completion must be established from a `status: verified, target_met: true` live report.

Types are disjoint classifications from Amazon metadata leaf categories, defined in `data/scripts/expansion_types.py`: headphones; Bluetooth speakers; keyboards; computer mice; displays; Wi-Fi routers; cameras; charging and USB cables; solid-state drives; external hard drives; USB flash drives; memory cards; power strips and surge protectors; USB hubs; HDMI cables. Displays include monitors and televisions; cameras include webcams and several camera categories. Product = parent_asin, not a color/size variant. Original Beats Studio Buds have an empty upstream category array; the already-inspected title establishes the explicit headphone override. Metadata categories themselves are not fabricated or rewritten.

## Source and selection

Source remains McAuley Lab's [Amazon Reviews 2023](https://amazon-reviews-2023.github.io/), pinned Hugging Face revision `2b6d039ed471f2ba5fd2acb718bf33b0a7e5598e`, files `raw/meta_categories/meta_Electronics.jsonl` and `raw/review_categories/Electronics.jsonl`. Full metadata discovery identifies candidates with at least 5,000 metadata ratings, plus the original products. That rating count is a discovery heuristic, not the number of usable source texts. No rating filter is applied to review selection.

New reviews require at least 5 whitespace-separated words, at most 1,000 text characters and at most 300 title characters. Full text of a selected review is preserved; longer reviews are excluded rather than truncated. The original 547 records are exempt and preserved byte-for-byte at the document-field level. This bounded text policy allows broad real-feedback coverage within the user storage budget. It favors concise reviews, is not a representative population sample, and is not an issue-quality classifier.

Each candidate retains 1,000 unique content-addressed reviews and must have at least one review in each A/B/C batch. The first eligible source rows are used; when a batch is absent, a non-original record from an overrepresented batch can be replaced locally to establish chronological representation. No already imported review is removed. Final selections are ranked using metadata rating_number, while original products are mandatory. Once a complete 20-product type is staged, its IDs are frozen in `locked-products.json`; later stages add types, not replace existing product groups.

The source can require scanning much more data than is stored. Only bounded candidate subsets and checkpoints are saved locally in ignored `data/raw/expansion300`. Media and reviewer identifiers are omitted. A source row may be rejected for invalid timestamps/ratings; counters are included in scan progress. Empty or very short comments are excluded. No reviews, findings, decisions or labels are fabricated to reach the target.

## Time and provenance

Original A/B cutoffs stay fixed: A ends `2022-04-28T18:33:34.534Z`; B ends `2022-10-27T01:13:13.233Z`. C follows B and remains held out. Expansion may add older history to A and later historical source reviews to C; it never moves an existing review across batches. A/B/C durations and per-product counts can be very unequal. Use actual date windows and denominators for comparisons; do not compare raw batch totals as if exposure were equal.

The dataset ID `amazon-reviews-2023-electronics-headphones-v1` remains a stable legacy identifier and is not a category restriction. The three global batch IDs are preserved. New products have `product_type` in addition to source title/store/categories/provenance. Compact new review provenance stores upstream `revision` and 1-based `line`; `batch.source_manifest` supplies source repository and exact review/metadata paths. The review `_id` already contains the content SHA-256, so the expanded records do not repeat that hash or long source URL in every review. Original full provenance is unchanged.

C is excluded from initial sample files and earlier memory. This task performs no LLM extraction or Hindsight memory writes. The supplied Hindsight endpoint/key is only stored in the ignored local environment for the backend task. Do not put C review outcomes, evaluation labels or later decisions into A/B analysis memory.

## Safe import and capacity

`import_expansion300.py` validates all existing source review contents against the prepared bundle and derives existing batch counts/dates from persisted membership before writes. Conflicting records, manifests or batch counters stop the importer. Existing scoped analysis runs also stop expansion to avoid silently changing a used evaluation cohort. Product classification is an additive compare-and-set. No user collection, index or distinct review is deleted.

A renewable ingestion lease lives on Batch A and serializes these expansion writers. Each transaction fences its lease ownership and expiry before adding missing reviews and incrementing their batch counters together. A failed or interrupted chunk cannot leave review membership and counters split. Reruns validate existing records and only upsert missing records. A second pass must insert zero records. Live application writers should remain paused during expansion or honor the same lease; the lease cannot coordinate unrelated writers that ignore it.

Atlas Free quota uses **uncompressed `dataSize` + `indexSize` across non-system databases**, not compressed `storageSize`. The importer requires full database visibility, checks the full prepared increment plus an index allowance up front, rechecks quota before each chunk with an 8 MB buffer and index allowance, and verifies the final total. It stops rather than crossing the user ceiling or provisioning paid capacity. Report physical storage separately from quota usage. [MongoDB Free cluster limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/)

## Reproduction

From the repository root, after the initial schema/index setup and secure MongoDB configuration:

```bash
.venv/bin/python data/scripts/discover_types.py
.venv/bin/python -u data/scripts/scan_expansion300.py
# After an interruption, resume the exact pinned stream using the saved checkpoint:
.venv/bin/python -u data/scripts/scan_expansion300.py --resume
.venv/bin/python data/scripts/prepare_expansion300.py
.venv/bin/python -u data/scripts/import_expansion300.py
```

For staged transfer while a scan is still running, use `select_expansion_stage.py` after a checkpoint is available, followed by preparation and import. It freezes only complete 20-product groups and preserves all previously frozen groups. Do not overwrite a prepared bundle or start a second importer while its current stage is running. Reports for verified stages are retained under `data/processed/expansion300/stages`.

Progress: `data/raw/expansion300/progress.json` for source selection; `data/processed/expansion300/import-progress.json` for persisted import progress. Final evidence: `data/processed/expansion300/mongodb-validation.json`. `stage_verified` means a partial set of complete product types has been verified; it is not completion of the 300-product target.

Validation checks unique IDs, full persisted review equality, originals preserved, product/batch references, exactly 1,000 reviews per selected product, 20 products per selected type, strict global chronology, derived batch counters, current indexes, query plans and second-pass idempotence. Reports contain no credentials.

## Redundancy audit

Before expansion, the live database contained 547 unique reviews, no exact duplicate groups and no orphan reviews. All 11 indexes served distinct identity or required query purposes; zero records or indexes were deleted. Expansion uses the same unique content identities to avoid inserting duplicates. Shared provenance is stored once per batch to reduce repeated metadata in new review records.
