# MongoDB data layer implementation plan

Goal: reproducible, bounded ingestion of real headphone reviews into MongoDB, preserving historical evaluation isolation.

Architecture: stream a pinned revision of the official McAuley Lab dataset; retain only headphone candidates; select 3–5 products and 500–1,500 substantive reviews. Normalize and content-address reviews, partition with shared time cutoffs, then safely insert missing documents into six validated collections. Existing conflicts stop the loader before writes. No git history changes or remote synchronization are part of this task.

1. Inspect metadata and review keys; record revision and scanned range; choose products with temporal spread and recurring concern vocabulary (not inferred findings).
2. Test timestamp aliases/units, content identity, collision/deduplication, global batch boundaries and evaluation gating. Implement normalization and local validation.
3. Implement MongoDB schema/index contracts and safe import: preflight existing collections, detect conflicting records, insert-only idempotent upserts; repeat import and verify persisted data, references, counts and query plans.
4. Document selection bias, exact reproduction, attribution, schemas/index order, chronological evaluation policy and actual ingestion status.

Review focus: mismatched field aliases; timestamp units; equal timestamps at cutoffs; hash collision/differing records; existing incompatible MongoDB schema/indexes; access to C before evaluation. MongoDB connection is required to verify live persistence; local validation cannot substitute for that.
