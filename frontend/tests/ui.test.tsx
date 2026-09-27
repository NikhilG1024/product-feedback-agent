import { it, expect, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Reviewer } from "../src/Reviewer";
import { Report } from "../src/Report";
import { DemoApi } from "../src/demo";
import type { Run, Submission } from "../src/types";
const product = {
  id: "demo-headphones",
  title: "Forma Studio Wireless",
  product_type: "Headphones",
};
it("review is saved independently of memory completion", async () => {
  const user = userEvent.setup();
  render(<Reviewer api={new DemoApi()} product={product} onSaved={() => {}} />);
  await user.click(screen.getByRole("radio", { name: "4 stars" }));
  await user.type(screen.getByLabelText("Review title"), "Comfortable");
  await user.type(
    screen.getByLabelText("Your experience"),
    "Good sound during calls.",
  );
  await user.click(screen.getByRole("button", { name: "Submit review" }));
  expect(await screen.findByText("Your review is saved.")).toBeInTheDocument();
  expect(screen.getByText("Preparing feedback for the AI")).toBeInTheDocument();
  expect(screen.queryByText("Analysis complete")).not.toBeInTheDocument();
});
it("starts a fresh review without changing the saved review or reusing its key", async () => {
  const api = new DemoApi();
  const submit = vi.spyOn(api, "submit");
  const onSaved = vi.fn();
  const user = userEvent.setup();
  render(<Reviewer api={api} product={product} onSaved={onSaved} />);
  await user.click(screen.getByRole("radio", { name: "4 stars" }));
  await user.type(screen.getByLabelText("Review title"), "First review");
  await user.type(screen.getByLabelText("Your experience"), "First review body.");
  await user.click(screen.getByRole("button", { name: "Submit review" }));
  expect(await screen.findByText("Your review is saved.")).toBeInTheDocument();
  const firstId = (await api.status((await submit.mock.results[0].value).id)).id;
  await user.click(screen.getByRole("button", { name: "Write another review" }));
  expect(screen.getByRole("heading", { name: "How was your experience?" })).toHaveFocus();
  expect(screen.getByLabelText("Review title")).toHaveValue("");
  expect(screen.getByLabelText("Your experience")).toHaveValue("");
  expect(screen.getByRole("radio", { name: "4 stars" })).not.toBeChecked();
  await user.click(screen.getByRole("radio", { name: "2 stars" }));
  await user.type(screen.getByLabelText("Review title"), "Second review");
  await user.type(screen.getByLabelText("Your experience"), "Second review body.");
  await user.click(screen.getByRole("button", { name: "Submit review" }));
  expect(await screen.findByText("Your review is saved.")).toBeInTheDocument();
  const secondId = (await submit.mock.results[1].value).id;
  expect(secondId).not.toBe(firstId);
  expect(submit.mock.calls[0][2]).not.toBe(submit.mock.calls[1][2]);
  expect((await api.status(firstId)).id).toBe(firstId);
  expect(onSaved).toHaveBeenCalledTimes(2);
});
it("receives summary inclusion on the saved review stream after memory processing finishes", async () => {
  const api = new DemoApi();
  Object.defineProperty(api, "demo", { value: false });
  const saved = { id: "saved-review", processing: { status: "completed", attempts: 1,
    memory_status: "synced", classification_status: "completed" },
    summary: { status: "needs_initial_summary" as const, version: null } };
  vi.spyOn(api, "submit").mockResolvedValue(saved);
  let push!: (submission: Submission) => void;
  const watch = vi.fn((_id: string, onSubmission: (submission: Submission) => void) => { push = onSubmission; return new Promise<void>(() => {}); });
  Object.assign(api, { watchSubmission: watch });
  const status = vi.spyOn(api, "status");
  const user = userEvent.setup();
  render(<Reviewer api={api} product={product} onSaved={() => {}} />);
  await user.click(screen.getByRole("radio", { name: "4 stars" }));
  await user.type(screen.getByLabelText("Review title"), "Battery");
  await user.type(screen.getByLabelText("Your experience"), "Long life.");
  await user.click(screen.getByRole("button", { name: "Submit review" }));
  expect(await screen.findByText("Initial summary not published yet")).toBeInTheDocument();
  await act(async () => { push({ ...saved, summary: { status: "included", version: 2 } }); });
  expect(screen.getByText("Included in version 2")).toBeInTheDocument();
  expect(watch).toHaveBeenCalledTimes(1);
  expect(status).not.toHaveBeenCalled();
});

it.each([
  ["needs_initial_summary", "Initial summary not published yet"],
  ["waiting", "Summary update pending"],
  ["queued", "Update queued"],
  ["updating", "Updating"],
  ["failed", "Update failed; review saved"],
] as const)("shows the server's %s summary state after saving", async (state, label) => {
  const api = new DemoApi();
  vi.spyOn(api, "submit").mockResolvedValue({ id: `review-${state}`, processing: null,
    summary: { status: state, version: null } });
  const user = userEvent.setup();
  render(<Reviewer api={api} product={product} onSaved={() => {}} />);
  await user.click(screen.getByRole("radio", { name: "4 stars" }));
  await user.type(screen.getByLabelText("Review title"), "Battery");
  await user.type(screen.getByLabelText("Your experience"), "Battery life changed.");
  await user.click(screen.getByRole("button", { name: "Submit review" }));
  expect(await screen.findByText(label)).toBeInTheDocument();
});
it("does not render report content until completed", () => {
  const run: Run = {
    id: "r",
    parent_asin: "p",
    mode: "memory",
    created_at: "2026-09-27T00:00:00Z",
    available_through: "2026-09-27T00:00:00Z",
    snapshot_hash: "x",
    stale: false,
    trend: null,
    limitations: null,
    investigation_suggestions: null,
    status: "running",
    summary: "MUST NOT SHOW",
    supporting_review_counts: [
      { theme: "SECRET", finding_id: "1", supporting_review_count: 5 },
    ],
    guidance_references: [],
    scope: { source: "amazon_2023", evaluation: false },
    denominator: 10,
  };
  render(<Report run={run} demo={false} onEvidence={vi.fn()} />);
  expect(screen.queryByText("MUST NOT SHOW")).not.toBeInTheDocument();
  expect(screen.queryByText("SECRET")).not.toBeInTheDocument();
});
it("retries the identical failed review with the same key", async () => {
  const api = new DemoApi();
  const real = api.submit.bind(api);
  const keys: string[] = [];
  let calls = 0;
  vi.spyOn(api, "submit").mockImplementation(async (p, b, k) => {
    keys.push(k);
    if (++calls === 1) throw Error("lost response");
    return real(p, b, k);
  });
  const user = userEvent.setup();
  render(<Reviewer api={api} product={product} onSaved={() => {}} />);
  await user.click(screen.getByRole("radio", { name: "3 stars" }));
  await user.type(screen.getByLabelText("Review title"), "Battery");
  await user.type(
    screen.getByLabelText("Your experience"),
    "Lasts a few hours.",
  );
  await user.click(screen.getByRole("button", { name: "Submit review" }));
  await screen.findByRole("alert");
  await user.click(screen.getByRole("button", { name: "Retry submission" }));
  await waitFor(() => expect(keys).toHaveLength(2));
  expect(keys[0]).toBe(keys[1]);
});

it("allows correcting a draft after a definite server rejection", async () => {
  const { ApiError } = await import("../src/api");
  const api = new DemoApi();
  vi.spyOn(api, "submit").mockRejectedValue(
    new ApiError(422, "invalid_request"),
  );
  const user = userEvent.setup();
  render(<Reviewer api={api} product={product} onSaved={() => {}} />);
  await user.click(screen.getByRole("radio", { name: "3 stars" }));
  await user.type(screen.getByLabelText("Review title"), "Title");
  await user.type(screen.getByLabelText("Your experience"), "Some feedback.");
  await user.click(screen.getByRole("button", { name: "Submit review" }));
  await screen.findByRole("alert");
  await user.click(screen.getByRole("button", { name: "Correct draft" }));
  expect(screen.getByLabelText("Review title")).toBeEnabled();
});

it("cannot clear the review idempotency key while a retry is pending", async () => {
  const { ApiError } = await import("../src/api");
  const api = new DemoApi();
  let rejectRetry: (e: unknown) => void = () => {};
  const pending = new Promise<never>((_, reject) => {
    rejectRetry = reject;
  });
  vi.spyOn(api, "submit")
    .mockRejectedValueOnce(new ApiError(429, "rate_limited"))
    .mockReturnValueOnce(pending);
  const user = userEvent.setup();
  render(<Reviewer api={api} product={product} onSaved={() => {}} />);
  await user.click(screen.getByRole("radio", { name: "3 stars" }));
  await user.type(screen.getByLabelText("Review title"), "Title");
  await user.type(screen.getByLabelText("Your experience"), "Some feedback.");
  await user.click(screen.getByRole("button", { name: "Submit review" }));
  await screen.findByRole("button", { name: "Correct draft" });
  await user.click(screen.getByRole("button", { name: "Retry submission" }));
  const edit = screen.queryByRole("button", { name: "Correct draft" });
  if (edit) expect(edit).toBeDisabled();
  rejectRetry(new ApiError(0, "network"));
  await screen.findByRole("alert");
});

it("defaults analysis to five reviews and allows the full group", async () => {
  const { AnalysisDialog } = await import("../src/AnalysisDialog");
  const user = userEvent.setup();
  const api = new DemoApi();
  const analyze = vi.spyOn(api, "analyze");
  render(<AnalysisDialog api={api} product={product} onClose={vi.fn()} onQueued={vi.fn()} />);
  expect(screen.getByLabelText("Analysis size")).toHaveValue("demo");
  await waitFor(() => expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "Start analysis" }));
  await waitFor(() => expect(analyze).toHaveBeenCalled());
  expect(analyze.mock.calls[0][1].scope.sample_size).toBe(5);
});

it("discovers review groups from one metadata request without paging through review text", async () => {
  const { AnalysisDialog } = await import("../src/AnalysisDialog");
  const api = new DemoApi();
  const reviews = vi.spyOn(api, "reviews").mockImplementation(async (_product, _source, _batch, cursor) => ({
    items: [{ id: cursor || "page-0", parent_asin: product.id, asin: product.id, title: "Review", text: "Long review",
      rating: 3, timestamp: "2023-06-12T00:00:00Z", source: "amazon_2023", batch_id: "demo:B", processing: null }],
    next_cursor: cursor === "page-9" ? null : `page-${cursor ? Number(cursor.slice(5)) + 1 : 1}`,
  }));
  const batches = vi.fn().mockResolvedValue({ items: [{ id: "demo:B", label: "B", review_count: 1000 }] });
  Object.assign(api, { reviewBatches: batches });
  render(<AnalysisDialog api={api} product={product} onClose={vi.fn()} onQueued={vi.fn()} />);
  expect(await screen.findByRole("combobox", { name: "Review group" })).toHaveValue("demo:B");
  expect(batches).toHaveBeenCalledTimes(1);
  expect(reviews).not.toHaveBeenCalled();
});

it("full group selection warns about duration and omits sampling", async () => {
  const { AnalysisDialog } = await import("../src/AnalysisDialog");
  const user = userEvent.setup();
  const api = new DemoApi();
  const analyze = vi.spyOn(api, "analyze");
  render(<AnalysisDialog api={api} product={product} onClose={vi.fn()} onQueued={vi.fn()} />);
  await user.selectOptions(screen.getByLabelText("Analysis size"), "full");
  expect(screen.getByText("Large groups may take hours with free-tier pacing.")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "Start analysis" }));
  await waitFor(() => expect(analyze).toHaveBeenCalled());
  expect(analyze.mock.calls[0][1].scope.sample_size).toBeUndefined();
});
