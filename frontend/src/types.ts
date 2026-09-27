export type Source = "amazon_2023" | "user_submission";
export type Mode = "baseline" | "memory";
export interface Product {
  id: string;
  title: string;
  product_type: string | null;
}
export type InitializationProductStatus = "queued" | "generating" | "citation_checks_passed" | "reused" | "needs_review";
export interface InitializationProgressResponse {
  availability: "available" | "stale" | "unavailable";
  progress: null | {
    run_id: string;
    started_at: string;
    updated_at: string;
    status: "running" | "paused_quality_review" | "completed" | "completed_with_failures";
    pause_reason?: string | null;
    total: number;
    workers: number;
    completed: number;
    reused?: number;
    failed: number;
    active: number;
    queued: number;
    published: null;
    elapsed_seconds?: number;
    products: { id: string; title: string; status: InitializationProductStatus; elapsed_seconds?: number }[];
    next_offset: number | null;
  };
}
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}
export interface Processing {
  status: string;
  attempts: number;
  error_code?: string | null;
  memory_status: string | null;
  classification_status: string | null;
}
export interface Submission {
  id: string;
  processing: Processing | null;
  summary?: ReviewerSummaryStatus | null;
}
export interface ReviewerSummaryStatus {
  status: "saved" | "waiting" | "queued" | "updating" | "included" | "failed";
  version: number | null;
}
export interface ReviewInput {
  title: string;
  text: string;
  rating: number;
}
export interface Review extends ReviewInput {
  id: string;
  parent_asin: string;
  asin: string;
  timestamp: string;
  source: Source;
  batch_id: string | null;
  processing: Processing | null;
}
export interface ReviewListOptions {
  sentiment?: "positive" | "neutral" | "negative";
  rating?: 1 | 2 | 3 | 4 | 5;
  sort?: "priority" | "newest";
  limit?: number;
}
export interface Scope {
  sample_size?: 5 | null;
  source: Source;
  batch_id?: string;
  available_through?: string;
  evaluation: false;
}
export interface AnalysisInput {
  mode: Mode;
  scope: Scope;
}
export interface GuidanceReference {
  decision_id: string;
  kind: string;
  rationale: string;
}
export interface Run {
  id: string;
  parent_asin: string;
  mode: Mode;
  scope: Scope;
  status: string;
  error_code?: string | null;
  created_at: string;
  available_through: string;
  snapshot_hash: string;
  denominator: number;
  stale: boolean;
  summary: string | null;
  supporting_review_counts:
    | { finding_id: string; theme: string; supporting_review_count: number }[]
    | null;
  guidance_references: GuidanceReference[] | null;
  trend: null;
  limitations: string[] | null;
  investigation_suggestions: string[] | null;
}
export interface Finding {
  id: string;
  issue_type: string;
  theme: string;
  description: string;
  evidence: { review_id: string; quote: string }[];
  review_ids: string[];
  supporting_review_count: number;
  provenance_validated: boolean;
  semantic_support: string;
  evidence_sampled: boolean;
}
export interface Findings {
  run_id: string;
  denominator: number;
  items: Finding[];
}
export interface DecisionInput {
  kind: "correction" | "decision" | "preference";
  rationale: string;
  evidence_ids: string[];
  available_through?: string;
}
export interface Decision {
  id: string;
  parent_asin: string;
  kind: string;
  rationale: string;
  evidence_ids: string[];
  decided_at: string;
  available_through: string;
  processing: Processing | null;
}
export interface Api {
  summaryProgress(offset?: number): Promise<InitializationProgressResponse>;
  summary(product: string): Promise<SummaryView>;
  summaryHistory(product: string, cursor?: string): Promise<Page<SummaryVersion>>;
  summaryVersion(product: string, version: number): Promise<SummaryVersion>;
  summarySettings(product: string, threshold: number): Promise<SummaryView>;
  refreshSummary(product: string, reason: "pending_reviews" | "guidance", key: string): Promise<SummaryView>;
  summaryQuestion(product: string, version: number, question: string): Promise<Answer>;
  question(product: string, run: string, question: string): Promise<Answer>;
  readonly demo: boolean;
  products(cursor?: string): Promise<Page<Product>>;
  reviews(
    product: string,
    source?: Source,
    batch?: string,
    cursor?: string,
    options?: ReviewListOptions,
  ): Promise<Page<Review>>;
  submit(product: string, body: ReviewInput, key: string): Promise<Submission>;
  status(id: string): Promise<Submission>;
  analyze(product: string, body: AnalysisInput): Promise<Run>;
  run(id: string): Promise<Run>;
  findings(product: string, run: string): Promise<Findings>;
  decision(product: string, body: DecisionInput): Promise<Decision>;
  decisions(product: string): Promise<Decision[]>;
}

export interface SummaryEvidencePair { review_id: string; quote: string }
export interface SummaryTheme {
  id: string;
  description: string;
  issue_type: "reported_defect" | "preference" | "feature_request" | "other";
  polarity: "positive" | "negative" | "mixed" | "neutral";
  evidence: SummaryEvidencePair[];
}
export interface SummaryVersion {
  product_id: string;
  version: number;
  parent_version: number | null;
  job_id: string;
  kind: "initial" | "reviews" | "guidance";
  narrative: string;
  themes: SummaryTheme[];
  contradictions: string[];
  coverage: { historical_sample_count: number; new_review_count: number };
  delta_review_ids: string[];
  manifest_ref: string | null;
  model_identity: string;
  prompt_version: string;
  guidance_references: string[];
  created_at: string;
  published_at: string | null;
  semantic_review?: { status: "pending" | "approved" | "rejected" };
}
export interface SummaryView {
  product_id: string;
  current: SummaryVersion | null;
  initial_candidate?: SummaryVersion | null;
  last_updated_at: string | null;
  update_threshold: number;
  pending_review_count: number;
  status: "uninitialized" | "waiting" | "queued" | "updating" | "ready" | "failed";
  error_code: string | null;
  memory_status: string;
}

export interface Answer {
  answer: string;
  evidence: { review_id: string; quote: string }[];
  insufficient_evidence: boolean;
}
