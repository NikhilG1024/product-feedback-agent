export type Source = "amazon_2023" | "user_submission";
export type Mode = "baseline" | "memory";
export interface Product {
  id: string;
  title: string;
  product_type: string | null;
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
  question(product: string, run: string, question: string): Promise<Answer>;
  readonly demo: boolean;
  products(cursor?: string): Promise<Page<Product>>;
  reviews(
    product: string,
    source?: Source,
    batch?: string,
    cursor?: string,
  ): Promise<Page<Review>>;
  submit(product: string, body: ReviewInput, key: string): Promise<Submission>;
  status(id: string): Promise<Submission>;
  analyze(product: string, body: AnalysisInput): Promise<Run>;
  run(id: string): Promise<Run>;
  findings(product: string, run: string): Promise<Findings>;
  decision(product: string, body: DecisionInput): Promise<Decision>;
  decisions(product: string): Promise<Decision[]>;
}

export interface Answer {
  answer: string;
  evidence: { review_id: string; quote: string }[];
  insufficient_evidence: boolean;
}
