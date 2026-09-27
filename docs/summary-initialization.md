# Importing local initialization drafts

The initializer stages generated drafts in `product_summary_versions`, records selected membership in `product_summary_inputs`, and creates `product_summary_state` with no public current pointer. It never publishes a candidate or calls an inference provider. Runtime generation remains owned by the initialization task; this importer consumes its frozen artifacts.

Load the server environment securely before invoking the module from `backend`:

```sh
.venv/bin/python -m app.summaries.initialization \
  --manifest /absolute/path/sample-manifest.json \
  --reviews /absolute/path/sampled-reviews.jsonl \
  --artifacts /absolute/path/generated-summaries \
  --artifacts /absolute/path/corrective-pilot \
  --rejections /absolute/path/persistence-rejections.json \
  --report /absolute/path/preflight.json
```

This default is read-only: it validates all artifacts, source membership and hashes against live reviews, then checks global capacity. Apply the reviewed additive v3 migration separately; add `--apply` to stage after preflight. Repeating the import reuses stable product/job identity and existing membership. Submitted reviews retain independent pending membership; no unsampled historical review is admitted.

The source JSONL has one product object per line with `product_id` and ordered `reviews`. The manifest records product `_id`, eligible count, selected IDs, sample size and SHA256, random seed, sample time and A/B cutoff. Select exactly `min(20, eligible_count)` without replacement. Each imported historical source must match the live database and exclude C/held-out/submitted records. Live source readback finishes before writes. Imported historical records are expected to be immutable; concurrent historical mutation is unsupported.

Draft artifacts contain model identity, prompt version/hash, creation time, selected source IDs, alias mapping, narrative and evidence themes. `prepare_artifact` normalizes the generated shape into the architecture contract, resolves aliases to full IDs, verifies every quote and refuses truncation. Theme IDs are deterministic within this immutable candidate; taxonomy quality remains subject to semantic review. Coverage is computed from source membership. Neither embedded approval text nor passed lexical checks approves publication.

The repository stages normalized `GeneratedSummary` bytes. Its `raw_artifact_sha256` hashes those normalized bytes, **not the producer file or model weights**. The internal `model_manifest.import_provenance` preserves the original file hash, complete source artifact, sampling manifest identity, per-product sample entry and normalization version. For split GGUFs, the complete upstream manifest and both weight hashes are retained. Provenance is internal and must not be exposed as a client filesystem API.

Optional rejection input is a map from exact original producer-file SHA256 to a rejection audit with reviewer identity/type, review timestamp and rubric version. Only rejection records are accepted; approvals cannot enter through this path. Full rejection details remain internal provenance. The decision is explicitly mapped to normalized content bytes; unknown rubric outcomes remain absent. Reruns must preserve an already applied decision. Pending/unreviewed drafts and rejected drafts remain distinct, and all remain unpublished.

Current initialization cohort: 300 bulk artifacts, six alternative candidates for six of those products, 6,000 unique selected review IDs. Thirteen candidate artifacts have automated rejection audits; all other artifacts remain pending. Human review has not occurred. These counts are observations of this cohort, not hard-coded importer limits.
