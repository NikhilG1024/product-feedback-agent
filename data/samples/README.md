# Small real examples

`products.jsonl`: three metadata summaries joined using parent_asin (stored as product `_id`).
`reviews_A.jsonl`: earliest two Batch A reviews per selected product (six total), preserving real text and source provenance. No reviewer IDs or media payloads. These examples are not the complete dataset and must not be treated as a valid full import bundle.

Attribution: McAuley Lab, Amazon Reviews 2023; Hou et al. (2024), Bridging Language and Items for Retrieval and Recommendation. Source: https://amazon-reviews-2023.github.io/

The complete 547-review subset is in ignored `data/processed`; reproduce it with `data/scripts/extract.py --download`. Batch C is deliberately absent from the small samples.
