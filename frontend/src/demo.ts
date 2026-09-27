import { ApiError } from "./api";
import type {
  Api,
  AnalysisInput,
  Decision,
  DecisionInput,
  Finding,
  Review,
  ReviewInput,
  Run,
  Source,
  Submission,
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
  async reviews(
    product: string,
    source: Source = "amazon_2023",
    batch?: string,
  ) {
    if (source === "user_submission")
      return {
        items: clone(this.newReviews.filter((r) => r.parent_asin === product)),
        next_cursor: null,
      };
    return {
      items: [0, 1, 2, 3].map((i): Review => ({
        id: `${product}-review-${i}`,
        parent_asin: product,
        asin: product,
        title: "Sample review",
        text: quotes[product][i],
        rating: 3,
        timestamp: "2023-06-12T00:00:00Z",
        source,
        batch_id: batch || "demo:B",
        processing: null,
      })),
      next_cursor: null,
    };
  }
  async submit(product: string, body: ReviewInput, key: string) {
    const digest = JSON.stringify({ product, body });
    if (this.bodies.has(key)) {
      if (this.bodies.get(key) !== digest)
        throw new ApiError(409, "idempotency_conflict");
      return clone(this.submissions.get(key)!);
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
    return clone(row);
  }
  async status(id: string) {
    const result = [...this.submissions.values()].find((r) => r.id === id);
    if (!result) throw new ApiError(404, "missing");
    return clone(result);
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
