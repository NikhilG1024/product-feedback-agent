# Chronological evaluation boundary

This is historical replay, not current customer feedback. The selected records span 2021-07-01 through 2023-03-21, within Amazon Reviews 2023's historical corpus.

| Batch | Role | Inclusive UTC first / last review | Reviews |
|---|---|---|---:|
| A | Discovery | 2021-07-01 00:27:42.511 / 2022-04-28 18:33:34.534 | 273 |
| B | Continued analysis and genuine team feedback | 2022-04-28 23:22:02.917 / 2022-10-27 01:13:13.233 | 137 |
| C | Held-out comparison | 2022-10-27 18:15:39.221 / 2023-03-21 01:40:52.724 | 137 |

Cutoffs use shared 50%/75% quantiles of distinct review timestamps, never per-product boundaries. Equal timestamps stay together; every earlier batch ends strictly before the next begins. Batch IDs are `amazon-reviews-2023-electronics-headphones-v1:A` (and B/C). `reviews.batch_id` is the sole membership assignment.

Use `pipeline.review_filter(product, 'A')` or `'B'` in the future application. C requires explicit `evaluation=True`. This is an application helper, not a database authorization boundary: direct MongoDB reads can bypass it. Keep database access server-side and enforce the same rule at future API/memory boundaries. An A run may see only A, a B run may see A/B, and the final C evaluation may analyze C while recalling only pre-C decisions/corrections. Filter memories by both product and `available_through` (historical cutoff), not today's wall-clock time. Never retain C outcomes during preparation of A/B memory or between baseline and memory comparison runs.

Freeze A/B memory before comparing the same C records in baseline and memory modes. Do not expose manual C labels to extraction prompts or memory. No evaluation labels, hypothetical human decisions, simulated findings, Hindsight integration, or measured memory improvements are supplied by this task. Future manual labeling must be stored separately with restricted evaluation access. Only Batch A examples are in `data/samples`.
