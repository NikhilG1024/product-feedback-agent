import { it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Reviewer } from "../src/Reviewer";
import { Report } from "../src/Report";
import { DemoApi } from "../src/demo";
import type { Run } from "../src/types";
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
it("continues checking summary inclusion after memory processing finishes", async () => {
  const api = new DemoApi();
  Object.defineProperty(api, "demo", { value: false });
  const saved = { id: "saved-review", processing: { status: "completed", attempts: 1,
    memory_status: "synced", classification_status: "completed" },
    summary: { status: "waiting" as const, version: null } };
  vi.spyOn(api, "submit").mockResolvedValue(saved);
  const status = vi.spyOn(api, "status").mockResolvedValue({ ...saved,
    summary: { status: "included", version: 2 } });
  const user = userEvent.setup();
  render(<Reviewer api={api} product={product} onSaved={() => {}} />);
  await user.click(screen.getByRole("radio", { name: "4 stars" }));
  await user.type(screen.getByLabelText("Review title"), "Battery");
  await user.type(screen.getByLabelText("Your experience"), "Long life.");
  await user.click(screen.getByRole("button", { name: "Submit review" }));
  expect(await screen.findByText("Summary update pending")).toBeInTheDocument();
  expect(await screen.findByText("Included in version 2", {}, { timeout: 6000 })).toBeInTheDocument();
  expect(status).toHaveBeenCalled();
}, 8000);

it.each([
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
