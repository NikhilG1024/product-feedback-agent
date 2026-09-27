# Signal demo mockups

Open index.html directly or serve it with a local HTTP server. Standalone HTML design preview; React implementation has not begun. All data and actions are illustrative, not live API results.

## Latest design direction

Keep the approved cobalt/slate palette and a simple two-item sidebar. The demo home shows one product, four customer issues with supporting review counts, and a suggested starting point. Metrics, filters, taxonomy labels, repeated explanations and grounded questions have been removed from the main screen to reduce information load. Evidence, analysis mode, and corrections open on demand. The reviewer view is a focused form.

## Demo walkthrough

1. See the most common issues for the sample product.
2. Select Battery loses capacity or See the reviews to inspect illustrative evidence.
3. Add a correction for future analysis; the simulated result explicitly says memory sync is pending.
4. Open Write a review and submit the sample form; saved, memory-pending and waiting-classification states remain distinct.

Product switching and analysis are mock interactions, not live integration. Preview navigation is not authorization. No bearer tokens, Hindsight credentials or LLM credentials are used. The eventual implementation must fetch products dynamically and use /api/v1 with server-enforced identity.

Verified in headless Chrome at desktop and 390px mobile widths: evidence open/close, navigation, reviewer submission, no script errors and no mobile horizontal overflow. API integration and production React build have not run.

## Product summary and visualization

Latest request: add a product-specific issue summary above Customer issues, with a directly labeled horizontal bar chart of supporting review counts. Prior guidance is disclosed on demand. The chart uses a common zero-to-50 review scale for the illustrative counts 42/31/18/12, all out of the 248-review sample; reviews can overlap across themes. Chart bars open evidence. No time trend or causal claim is implied.

Implementation requirement: summary generation in memory mode must retrieve eligible product/source/time-scoped Hindsight guidance and pass it to the LLM alongside validated findings/evidence. Counts remain backend-computed. Baseline must continue bypassing memory and display that guidance was not used. Surface unavailable/pending memory honestly; do not claim guidance was used without confirmed retrieval. A saved correction affects a later summary, never silently rewrites a completed report. Coordinate this with the backend task; mockup does not implement provider calls.

Latest UI revision: removed the bottom Customer issues list. The summary and clickable chart remain; chart bars open the evidence drawer.
