import { ApiError } from "./api";
import type {
  Api,
  AnalysisInput,
  Decision,
  DecisionInput,
  Finding,
  Review,
  ReviewInput,
  ReviewListOptions,
  Run,
  Source,
  Submission,
  InitializationProgressResponse,
  SummaryView,
  SummaryVersion,
} from "./types";
const products = [
  {
    id: "demo-headphones",
    title: "Forma Studio Wireless",
    product_type: "Headphones",
  },
  {
    id: "demo-speaker",
    title: "Arc Portable Speaker",
    product_type: "Speakers",
  },
  { id: "demo-light", title: "Vista Desk Light", product_type: "Lighting" },
];
const themes: Record<string, string[]> = {
  "demo-headphones": [
    "Battery loses capacity",
    "Uncomfortable on long calls",
    "Connection drops",
    "More sound settings",
  ],
  "demo-speaker": [
    "Battery runs down quickly",
    "Distortion at high volume",
    "Pairing takes several attempts",
    "More sound settings",
  ],
  "demo-light": [
    "Brightness is inconsistent",
    "Base feels unstable",
    "Switch is hard to find",
    "Warmer light options",
  ],
};
const quotes: Record<string, string[]> = {
  "demo-headphones": [
    "After six months, the battery only lasts a couple of hours. It used to last the whole workday.",
    "After an hour on a call, the headphones press too hard around my ears.",
    "The music cuts out when I switch from my laptop to my phone.",
    "I wish I could save my own equalizer settings.",
  ],
  "demo-speaker": [
    "The speaker needs charging again halfway through the evening.",
    "The sound gets distorted when I turn it all the way up.",
    "It takes several attempts to pair with my phone.",
    "Please add adjustable bass settings.",
  ],
  "demo-light": [
    "The light sometimes gets dimmer without me touching it.",
    "The base rocks when I adjust the arm.",
    "I have to feel around to find the switch in the dark.",
    "I would like a warmer light for evening reading.",
  ],
};
const clone = <T>(x: T): T => structuredClone(x);
export class DemoApi implements Api {
  readonly demo = true;
  private summaryVersions = new Map<string, SummaryVersion[]>();
  private thresholds = new Map<string, number>();
  private refreshKeys = new Map<string, string>();
  private versions(product: string): SummaryVersion[] {
    if (!products.some((p) => p.id === product)) throw new ApiError(404, "missing");
    if (!this.summaryVersions.has(product)) {
      const version: SummaryVersion = {
        product_id: product, version: 1, parent_version: null,
        job_id: `${product}-initial`, kind: "initial",
        narrative: `Illustrative summary for ${products.find((p) => p.id === product)!.title}. ${themes[product][0]} and ${themes[product][1].toLowerCase()} appear in this small sample.`,
        themes: themes[product].map((description, i) => ({
          id: `${product}-theme-${i}`, description,
          issue_type: i === 3 ? "feature_request" : "reported_defect",
          polarity: "negative", evidence: [{ review_id: `${product}-review-${i}`, quote: quotes[product][i] }],
        })),
        contradictions: [], coverage: { historical_sample_count: 4, new_review_count: 0 },
        delta_review_ids: [], manifest_ref: `${product}-sample`,
        model_identity: "illustrative-demo", prompt_version: "demo-1", guidance_references: [],
        created_at: "2026-09-01T12:00:00Z", published_at: "2026-09-01T12:00:00Z",
        semantic_review: { status: "approved" },
      };
      this.summaryVersions.set(product, [version]);
    }
    return this.summaryVersions.get(product)!;
  }
  private view(product: string): SummaryView {
    const versions = this.versions(product);
    const current = versions[versions.length - 1];
    const pending = this.newReviews.filter((r) => r.parent_asin === product && !versions.some((v) => v.delta_review_ids.includes(r.id))).length;
    return clone({ product_id: product, current, last_updated_at: current.published_at,
      update_threshold: this.thresholds.get(product) ?? 1,
      pending_review_count: pending, status: pending ? "waiting" : "ready",
      error_code: null, memory_status: "not_applicable" });
  }
  async summary(product: string) { return this.view(product); }
  async summaryHistory(product: string, cursor?: string) {
    const list = [...this.versions(product)].reverse();
    const offset = cursor ? Number(cursor) : 0;
    if (!Number.isSafeInteger(offset) || offset < 0) throw new ApiError(422, "invalid_cursor");
    return { items: clone(list.slice(offset, offset + 2)), next_cursor: offset + 2 < list.length ? String(offset + 2) : null };
  }
  async summaryVersion(product: string, version: number) {
    const found = this.versions(product).find((v) => v.version === version);
    if (!found) throw new ApiError(404, "missing");
    return clone(found);
  }
  async summarySettings(product: string, threshold: number) {
    if (!Number.isInteger(threshold) || threshold < 1 || threshold > 100) throw new ApiError(422, "invalid_threshold");
    this.thresholds.set(product, threshold);
    this.publishPending(product);
    return this.view(product);
  }
  private publishPending(product: string) {
    const view = this.view(product);
    if (view.pending_review_count < view.update_threshold) return;
    const parent = view.current!;
    const pending = this.newReviews.filter((r) => r.parent_asin === product && !this.versions(product).some((v) => v.delta_review_ids.includes(r.id)));
    const next: SummaryVersion = { ...clone(parent), version: parent.version + 1,
      parent_version: parent.version, job_id: crypto.randomUUID(), kind: "reviews",
      narrative: `${parent.narrative} ${pending.length} new review${pending.length === 1 ? "" : "s"} added in sample mode.`,
      coverage: { ...parent.coverage, new_review_count: parent.coverage.new_review_count + pending.length },
      delta_review_ids: pending.map((r) => r.id), created_at: new Date().toISOString(),
      published_at: new Date().toISOString(),
    };
    this.versions(product).push(next);
  }
  async refreshSummary(product: string, reason: "pending_reviews" | "guidance", key: string) {
    const digest = `${product}:${reason}`;
    if (this.refreshKeys.has(key)) {
      if (this.refreshKeys.get(key) !== digest) throw new ApiError(409, "idempotency_conflict");
      return this.view(product);
    }
    this.refreshKeys.set(key, digest);
    if (reason === "pending_reviews") {
      const current = this.thresholds.get(product) ?? 1;
      this.thresholds.set(product, 1);
      this.publishPending(product);
      this.thresholds.set(product, current);
    }
    return this.view(product);
  }
  async summaryQuestion(product: string, version: number, _question: string) {
    const selected = await this.summaryVersion(product, version);
    return { answer: `Illustrative answer based on version ${version}: ${selected.narrative}`,
      evidence: selected.themes.flatMap((t) => t.evidence).slice(0, 2), insufficient_evidence: false };
  }
  async summaryProgress(): Promise<InitializationProgressResponse> {
    return { availability: "unavailable", progress: null };
  }
  async question(product: string, run: string, _question: string) {
    const r = this.runs.get(run);
    if (!r || r.parent_asin !== product) throw new ApiError(404, "missing");
    if (r.status !== "completed") throw new ApiError(409, "run_not_completed");
    return {
      answer:
        "Sample mode does not generate AI answers. Connect to your workspace to ask questions grounded in the selected reviews.",
      evidence: [],
      insufficient_evidence: true,
    };
  }
  private runGuidance = new Map<string, Decision[]>();
  private runs = new Map<string, Run>();
  private bodies = new Map<string, string>();
  private submissions = new Map<string, Submission>();
  private notes = new Map<string, Decision[]>();
  private newReviews: Review[] = [];
  async products() {
    return { items: clone(products), next_cursor: null };
  }
  async reviewBatches(_product: string) {
    return { items: [{ id: "demo:B", label: "B", review_count: 4 }] };
  }
  async reviews(
    product: string,
    source?: Source,
    batch?: string,
    cursor?: string,
    options?: ReviewListOptions,
  ) {
    const historical = [0, 1, 2, 3].map((i): Review => ({
        id: `${product}-review-${i}`,
        parent_asin: product,
        asin: product,
        title: "Sample review",
        text: quotes[product][i],
        rating: 3,
        timestamp: "2023-06-12T00:00:00Z",
        source: "amazon_2023",
        batch_id: batch || "demo:B",
        processing: null,
      }));
    const rows = [...historical, ...this.newReviews.filter((r) => r.parent_asin === product)]
      .filter((r) => (!source || r.source === source) && (!batch || r.batch_id === batch))
      .filter((r) => !options?.rating || r.rating === options.rating)
      .filter((r) => !options?.sentiment || (options.sentiment === "negative" ? r.rating <= 2 : options.sentiment === "positive" ? r.rating >= 4 : r.rating === 3));
    if (options?.sort === "priority") rows.sort((a, b) =>
      Number(b.source === "user_submission") - Number(a.source === "user_submission") ||
      (a.rating <= 2 ? 0 : a.rating === 3 ? 1 : 2) - (b.rating <= 2 ? 0 : b.rating === 3 ? 1 : 2) ||
      b.timestamp.localeCompare(a.timestamp) || a.id.localeCompare(b.id));
    else if (options?.sort === "newest") rows.sort((a, b) => b.timestamp.localeCompare(a.timestamp) || a.id.localeCompare(b.id));
    const start = cursor ? Math.max(0, rows.findIndex((r) => r.id === cursor) + 1) : 0;
    const limit = options?.limit ?? 100;
    return { items: clone(rows.slice(start, start + limit)), next_cursor: rows[start + limit] ? rows[start + limit - 1].id : null };
  }
  async submit(product: string, body: ReviewInput, key: string) {
    const digest = JSON.stringify({ product, body });
    if (this.bodies.has(key)) {
      if (this.bodies.get(key) !== digest)
        throw new ApiError(409, "idempotency_conflict");
      return this.status(this.submissions.get(key)!.id);
    }
    const row: Submission = {
      id: crypto.randomUUID(),
      processing: {
        status: "pending",
        attempts: 0,
        memory_status: "pending",
        classification_status: "pending",
      },
    };
    this.bodies.set(key, digest);
    this.submissions.set(key, row);
    this.newReviews.push({
      ...body,
      id: row.id,
      parent_asin: product,
      asin: product,
      timestamp: new Date().toISOString(),
      source: "user_submission",
      batch_id: null,
      processing: row.processing,
    });
    this.publishPending(product);
    const view = this.view(product);
    row.summary = view.current?.delta_review_ids.includes(row.id)
      ? { status: "included", version: view.current.version }
      : { status: "waiting", version: null };
    this.submissions.set(key, row);
    return clone(row);
  }
  async status(id: string): Promise<Submission> {
    const result = [...this.submissions.values()].find((r) => r.id === id);
    if (!result) throw new ApiError(404, "missing");
    const review = this.newReviews.find((r) => r.id === id)!;
    const included = this.versions(review.parent_asin).find((v) => v.delta_review_ids.includes(id));
    return clone({ ...result, summary: included
      ? { status: "included", version: included.version } : { status: "waiting", version: null } });
  }
  async analyze(product: string, body: AnalysisInput) {
    const live = this.newReviews.filter((r) => r.parent_asin === product);
    if (body.scope.source === "user_submission" && !live.length)
      throw new ApiError(422, "empty_scope");
    const now = new Date().toISOString();
    const run: Run = {
      id: crypto.randomUUID(),
      parent_asin: product,
      ...clone(body),
      status: "pending",
      created_at: now,
      available_through: body.scope.available_through || now,
      snapshot_hash: crypto.randomUUID(),
      denominator: Math.min(body.scope.sample_size || Infinity, body.scope.source === "amazon_2023" ? 248 : live.length),
      stale: false,
      summary: null,
      supporting_review_counts: null,
      guidance_references: null,
      trend: null,
      limitations: null,
      investigation_suggestions: null,
    };
    this.runs.set(run.id, run);
    this.runGuidance.set(
      run.id,
      clone(
        (this.notes.get(product) || []).filter(
          (d) => d.processing?.memory_status === "synced",
        ),
      ),
    );
    return clone(run);
  }
  async run(id: string) {
    const r = this.runs.get(id);
    if (!r) throw new ApiError(404, "missing");
    if (r.status === "completed") return clone(r);
    r.status = "completed";
    const p = products.find((p) => p.id === r.parent_asin)!;
    const list = themes[p.id];
    const live = r.scope.source === "user_submission";
    r.summary = live
      ? `${p.title}: ${r.denominator} new review${r.denominator === 1 ? "" : "s"} received. This sample mode shows submitted feedback without pretending to run AI analysis.`
      : `${p.title}: ${list[0].toLowerCase()} is the most frequently mentioned issue in this sample. Customers also mention ${list[1].toLowerCase()} and ${list[2].toLowerCase()}. Some would like ${list[3].toLowerCase()}.`;
    r.supporting_review_counts = live
      ? []
      : list.map((theme, i) => ({
          finding_id: `${id}-${i}`,
          theme,
          supporting_review_count: r.scope.sample_size === 5 ? [3, 2, 1, 1][i] : [42, 31, 18, 12][i],
        }));
    r.guidance_references =
      r.mode === "memory"
        ? (this.runGuidance.get(id) || []).map((d) => ({
            decision_id: d.id,
            kind: d.kind,
            rationale: d.rationale,
          }))
        : [];
    r.limitations = [
      "All results here are illustrative sample data.",
      "A review may mention multiple issues. One sample cannot establish a time trend.",
    ];
    r.investigation_suggestions = [];
    return clone(r);
  }
  async findings(product: string, id: string) {
    const r = this.runs.get(id);
    if (!r || r.parent_asin !== product) throw new ApiError(404, "missing");
    if (r.status !== "completed") throw new ApiError(409, "run_not_completed");
    const items: Finding[] = (r.supporting_review_counts || []).map((f, i) => ({
      ...f,
      id: f.finding_id,
      issue_type:
        i === 3
          ? "feature_request"
          : i === 1
            ? "preference"
            : "reported_defect",
      description:
        "An illustrative customer-reported concern, not a confirmed technical fault.",
      evidence: [
        { review_id: `${product}-review-${i}`, quote: quotes[product][i] },
      ],
      review_ids: [`${product}-review-${i}`],
      provenance_validated: true,
      semantic_support: "model_interpretation",
      evidence_sampled: true,
    }));
    return { run_id: id, denominator: r.denominator, items };
  }
  async decision(product: string, body: DecisionInput) {
    const d: Decision = {
      id: crypto.randomUUID(),
      ...body,
      parent_asin: product,
      decided_at: new Date().toISOString(),
      available_through: body.available_through || new Date().toISOString(),
      processing: {
        status: "pending",
        attempts: 0,
        memory_status: "pending",
        classification_status: null,
      },
    };
    this.notes.set(product, [...(this.notes.get(product) || []), d]);
    return clone(d);
  }
  async decisions(product: string) {
    const notes = this.notes.get(product) || [];
    notes.forEach((d) => {
      if (d.processing) {
        d.processing.status = "completed";
        d.processing.memory_status = "synced";
      }
    });
    return clone(notes);
  }
}
