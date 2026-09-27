import type {
  Api,
  Answer,
  AnalysisInput,
  Decision,
  DecisionInput,
  Findings,
  Page,
  Product,
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
import { watchSSE } from "./sse";
const messages: Record<string, string> = {
  historical_batch_required: "Choose a review group for past Amazon reviews.",
  cutoff_after_batch_end:
    "Choose a date on or before the end of this review group, or clear the date to use its default cutoff.",
  legacy_analysis_unsupported:
    "This older report cannot be opened. Start a new analysis for this product.",
  model_input_too_large:
    "This request is larger than the model can accept. Ask the demo host to adjust the review scope or model budget.",
  review_exceeds_model_budget:
    "A review is too large for the configured model budget. Ask the demo host to adjust the scope or budget.",
  analysis_output_limit_exceeded:
    "Too many issues were found. Try a smaller review group.",
  service_unavailable:
    "This server feature is not ready yet. Please try again later.",
  narrower_scope_required:
    "Choose a smaller review group or an earlier date. An analysis can include up to 1,500 reviews.",
  empty_scope: "No reviews match this selection. Try another group or source.",
  provider_not_configured:
    "The AI service is not configured on the server yet. Ask the demo host to configure it.",
  batch_not_found:
    "This review group was not found. Choose a group from the selected product.",
  run_not_completed:
    "This analysis is still processing. Please check again shortly.",
  idempotency_conflict:
    "This submission key already belongs to different feedback. Start a new review.",
  capacity_exceeded:
    "The server cannot accept more data right now. Please contact the demo host.",
  evaluation_scope_required:
    "This review group is reserved for evaluation. Select another group.",
  future_cutoff: "Choose a date that is not in the future.",
};
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
  ) {
    super(
      messages[code] ??
        (status === 401
          ? "Your access token is missing or expired. Reconnect with a valid token."
          : status === 403
            ? "This token does not have permission for this action. Use the correct account token."
            : status === 429
              ? "Too many requests. Please wait a minute and try again."
              : status === 422
                ? "Check the fields and try again."
                : status === 503
                  ? "The server is not ready. Please try again shortly."
                  : status === 404
                    ? "This item or feature is not available on the server yet."
                    : status === 0
                      ? "Cannot reach the server. Check the connection, then try again."
                      : "Something went wrong. Please try again."),
    );
    this.name = "ApiError";
  }
}
export const errorMessage = (error: unknown) =>
  error instanceof ApiError
    ? error.message
    : "Something went wrong. Please try again.";
export class HttpApi implements Api {
  readonly demo = false;
  readonly localAutoAuth: boolean;
  private streamHeaders(): Record<string, string> { return this.localAutoAuth ? {} : { Authorization: `Bearer ${this.token}` }; }
  watchProduct(product: string, onEvent: (event: { type: "summary"; view: SummaryView } | { type: "reviews_changed"; revision: string }) => void, signal: AbortSignal) {
    return watchSSE(`/api/v1/products/${encodeURIComponent(product)}/events`, this.streamHeaders(), ({ event, data }) => {
      const value: unknown = JSON.parse(data);
      if (event === "summary" && value && typeof value === "object" && (value as SummaryView).product_id === product)
        onEvent({ type: "summary", view: value as SummaryView });
      if (event === "reviews_changed" && value && typeof value === "object" && typeof (value as { revision?: unknown }).revision === "string")
        onEvent({ type: "reviews_changed", revision: (value as { revision: string }).revision });
    }, signal, this.fetcher);
  }
  watchSubmission(id: string, onSubmission: (submission: Submission) => void, signal: AbortSignal) {
    return watchSSE(`/api/v1/reviews/${encodeURIComponent(id)}/events`, this.streamHeaders(), ({ event, data }) => {
      if (event !== "submission") return;
      const value: unknown = JSON.parse(data);
      if (value && typeof value === "object" && (value as Submission).id === id) onSubmission(value as Submission);
    }, signal, this.fetcher);
  }
  watchAnalysis(id: string, onRun: (run: Run) => void, signal: AbortSignal) {
    return watchSSE(`/api/v1/analysis-runs/${encodeURIComponent(id)}/events`, this.streamHeaders(), ({ event, data }) => {
      if (event !== "analysis") return;
      const value: unknown = JSON.parse(data);
      if (value && typeof value === "object" && (value as Run).id === id) onRun(value as Run);
    }, signal, this.fetcher);
  }
  summary(product: string) {
    return this.request<SummaryView>(`/products/${encodeURIComponent(product)}/summary`);
  }
  summaryHistory(product: string, cursor?: string) {
    const q = new URLSearchParams({ limit: "20" });
    if (cursor) q.set("cursor", cursor);
    return this.request<Page<SummaryVersion>>(`/products/${encodeURIComponent(product)}/summary/history?${q}`);
  }
  summaryVersion(product: string, version: number) {
    return this.request<SummaryVersion>(`/products/${encodeURIComponent(product)}/summary/versions/${version}`);
  }
  summarySettings(product: string, threshold: number) {
    return this.request<SummaryView>(`/products/${encodeURIComponent(product)}/summary/settings`, "PATCH", { update_threshold: threshold });
  }
  refreshSummary(product: string, reason: "pending_reviews" | "guidance", key: string) {
    return this.request<SummaryView>(`/products/${encodeURIComponent(product)}/summary/refresh`, "POST", { reason }, { "Idempotency-Key": key });
  }
  summaryQuestion(product: string, version: number, question: string) {
    return this.request<Answer>(`/products/${encodeURIComponent(product)}/summary/questions`, "POST", { version, question });
  }
  summaryProgress(offset = 0) {
    return this.request<InitializationProgressResponse>(
      `/summary-initialization/progress?offset=${offset}&limit=20`,
    );
  }
  question(product: string, run: string, question: string) {
    return this.request<Answer>(
      `/products/${encodeURIComponent(product)}/questions`,
      "POST",
      { run_id: run, question },
    );
  }
  constructor(
    private token: string,
    private fetcher: typeof fetch = fetch,
    localAutoAuth = false,
  ) { this.localAutoAuth = localAutoAuth; }
  private async request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    extra: Record<string, string> = {},
  ): Promise<T> {
    let response: Response;
    try {
      const fetcher = this.fetcher;
      response = await fetcher("/api/v1" + path, {
        method,
        headers: {
          ...(!this.localAutoAuth ? { Authorization: `Bearer ${this.token}` } : {}),
          ...(body ? { "Content-Type": "application/json" } : {}),
          ...extra,
        },
        body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(30000),
        cache: "no-store",
      });
    } catch {
      throw new ApiError(0, "network");
    }
    let data: unknown;
    try {
      data = await response.json();
    } catch {
      if (response.ok) throw new ApiError(502, "invalid_response");
    }
    if (!response.ok) {
      const code = (data as { error?: { code?: unknown } })?.error?.code;
      throw new ApiError(
        response.status,
        typeof code === "string" && Object.hasOwn(messages, code)
          ? code
          : "request_failed",
      );
    }
    return data as T;
  }
  products(cursor?: string) {
    const q = new URLSearchParams({ limit: "30" });
    if (cursor) q.set("cursor", cursor);
    return this.request<Page<Product>>("/products?" + q);
  }
  reviewBatches(product: string) {
    return this.request<{ items: import("./types").ReviewBatch[] }>(`/products/${encodeURIComponent(product)}/review-batches`);
  }
  reviews(product: string, source?: Source, batch?: string, cursor?: string, options?: ReviewListOptions) {
    const q = new URLSearchParams({ limit: String(options?.limit ?? 100) });
    if (source) q.set("source", source);
    if (batch) q.set("batch_id", batch);
    if (cursor) q.set("cursor", cursor);
    if (options?.sentiment) q.set("sentiment", options.sentiment);
    if (options?.rating) q.set("rating", String(options.rating));
    if (options?.sort) q.set("sort", options.sort);
    return this.request<Page<Review>>(
      `/products/${encodeURIComponent(product)}/reviews?${q}`,
    );
  }
  submit(product: string, body: ReviewInput, key: string) {
    return this.request<Submission>(
      `/products/${encodeURIComponent(product)}/reviews`,
      "POST",
      body,
      { "Idempotency-Key": key },
    );
  }
  status(id: string) {
    return this.request<Submission>(
      `/reviews/${encodeURIComponent(id)}/status`,
    );
  }
  analyze(product: string, body: AnalysisInput) {
    return this.request<Run>(
      `/products/${encodeURIComponent(product)}/analysis-runs`,
      "POST",
      body,
    );
  }
  run(id: string) {
    return this.request<Run>(`/analysis-runs/${encodeURIComponent(id)}`);
  }
  findings(product: string, run: string) {
    return this.request<Findings>(
      `/products/${encodeURIComponent(product)}/findings?run_id=${encodeURIComponent(run)}`,
    );
  }
  decision(product: string, body: DecisionInput) {
    return this.request<Decision>(
      `/products/${encodeURIComponent(product)}/decisions`,
      "POST",
      body,
    );
  }
  async decisions(product: string) {
    const data = await this.request<{ items: Decision[] }>(
      `/products/${encodeURIComponent(product)}/decisions?limit=100`,
    );
    return data.items;
  }
}

export const uncertainWrite = (error: unknown) =>
  !(error instanceof ApiError) ||
  error.status === 0 ||
  (error.status >= 500 &&
    ["request_failed", "invalid_response"].includes(error.code));

export const analysisFailureMessage = (code?: string | null) =>
  messages[code || ""] ||
  "No partial results are shown. Check the server and worker, then start a new analysis.";
