import { expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { DemoApi } from "../src/demo";
import { ApiError } from "../src/api";
import { SummaryDashboard } from "../src/SummaryDashboard";
import { SummarySettings } from "../src/SummarySettings";
import type { Product, SummaryView } from "../src/types";

const headphone: Product = { id: "demo-headphones", title: "Forma Studio Wireless", product_type: "Headphones" };
const speaker: Product = { id: "demo-speaker", title: "Arc Portable Speaker", product_type: "Speakers" };

it("loads only the cached summary on product selection and labels its actual sample", async () => {
  const api = new DemoApi();
  const summary = vi.spyOn(api, "summary");
  const history = vi.spyOn(api, "summaryHistory");
  const analyze = vi.spyOn(api, "analyze");
  render(<SummaryDashboard api={api} product={headphone} />);
  expect(await screen.findByText(/Based on 4 sampled historical reviews \+ 0 new reviews/)).toBeInTheDocument();
  expect(summary).toHaveBeenCalledWith(headphone.id);
  expect(analyze).not.toHaveBeenCalled();
  expect(history).not.toHaveBeenCalled();
  expect(screen.queryByText(/Based on 20/)).not.toBeInTheDocument();
  expect(screen.queryByText(/all reviews/i)).not.toBeInTheDocument();
});

it("keeps a published version visible when a newer update fails", async () => {
  const api = new DemoApi();
  const base = await api.summary(headphone.id);
  vi.spyOn(api, "summary").mockResolvedValue({ ...base, status: "failed", error_code: "generation_failed", pending_review_count: 1 });
  render(<SummaryDashboard api={api} product={headphone} />);
  expect(await screen.findByText(/Current published summary/)).toBeInTheDocument();
  expect(screen.getByText(/Summary update failed/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Battery loses capacity" })).toBeInTheDocument();
});

it("shows an explicit empty state before publication", async () => {
  const api = new DemoApi();
  const progress = vi.spyOn(api, "summaryProgress");
  vi.spyOn(api, "summary").mockResolvedValue({ product_id: headphone.id, current: null, last_updated_at: null, update_threshold: 1,
    pending_review_count: 0, status: "uninitialized", error_code: null, memory_status: "pending" });
  render(<SummaryDashboard api={api} product={headphone} />);
  expect(await screen.findByText("No published summary yet.")).toBeInTheDocument();
  expect(screen.queryByText("Product summary initialization")).not.toBeInTheDocument();
  expect(progress).not.toHaveBeenCalled();
});

it("shows a generated initial candidate without treating it as published", async () => {
  const api = new DemoApi();
  const published = (await api.summary(headphone.id)).current!;
  const candidate = { ...published, version: 3, published_at: null,
    semantic_review: { status: "pending" as const } };
  vi.spyOn(api, "summary").mockResolvedValue({ product_id: headphone.id, current: null,
    initial_candidate: candidate, last_updated_at: null, update_threshold: 1,
    pending_review_count: 2, status: "uninitialized", error_code: null, memory_status: "unknown" });
  const history = vi.spyOn(api, "summaryHistory");
  const question = vi.spyOn(api, "summaryQuestion");
  render(<SummaryDashboard api={api} product={headphone} />);
  expect(await screen.findByText("Generated initial summary")).toBeInTheDocument();
  expect(screen.getByText("Validation pending")).toBeInTheDocument();
  expect(screen.getByText(/Based on 4 sampled historical reviews \+ 0 new reviews/)).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Evidence for version 3" })).toBeInTheDocument();
  expect(screen.getByText(/New reviews will be incorporated after the initial summary is published/)).toBeInTheDocument();
  expect(screen.queryByText("No published summary yet.")).not.toBeInTheDocument();
  expect(screen.queryByText("Current published summary")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "View version history" })).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Question" })).not.toBeInTheDocument();
  expect(history).not.toHaveBeenCalled();
  expect(question).not.toHaveBeenCalled();
});

it("labels an approved initial candidate as awaiting publication", async () => {
  const api = new DemoApi();
  const base = await api.summary(headphone.id);
  vi.spyOn(api, "summary").mockResolvedValue({ ...base, current: null, last_updated_at: null,
    initial_candidate: { ...base.current!, published_at: null, semantic_review: { status: "approved" } } });
  render(<SummaryDashboard api={api} product={headphone} />);
  expect(await screen.findByText("Awaiting publication")).toBeInTheDocument();
  expect(screen.queryByText("Validation pending")).not.toBeInTheDocument();
  expect(screen.queryByText("Current published summary")).not.toBeInTheDocument();
});

it("replaces a generated candidate with the current published summary on poll", async () => {
  vi.useFakeTimers();
  try {
    const api = new DemoApi();
    Object.defineProperty(api, "demo", { value: false });
    const published = await api.summary(headphone.id);
    const candidateView: SummaryView = { ...published, current: null,
      initial_candidate: { ...published.current!, published_at: null,
        semantic_review: { status: "pending" } }, last_updated_at: null, status: "uninitialized" };
    vi.spyOn(api, "summary").mockResolvedValueOnce(candidateView).mockResolvedValue(published);
    render(<SummaryDashboard api={api} product={headphone} />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("Generated initial summary")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText("Current published summary")).toBeInTheDocument();
    expect(screen.queryByText("Generated initial summary")).not.toBeInTheDocument();
  } finally { vi.useRealTimers(); }
});

it("polls the selected product until its published summary appears", async () => {
  vi.useFakeTimers();
  try {
    const api = new DemoApi();
    Object.defineProperty(api, "demo", { value: false });
    const published = await api.summary(headphone.id);
    const empty: SummaryView = { ...published, current: null, last_updated_at: null, status: "uninitialized" };
    const summary = vi.spyOn(api, "summary").mockResolvedValueOnce(empty).mockResolvedValue(published);
    render(<SummaryDashboard api={api} product={headphone} />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("No published summary yet.")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText("Current published summary")).toBeInTheDocument();
    expect(summary).toHaveBeenCalledTimes(2);
  } finally {
    vi.useRealTimers();
  }
});

it("ignores obsolete product responses", async () => {
  const api = new DemoApi();
  let resolveFirst!: (value: SummaryView) => void;
  const first = new Promise<SummaryView>((resolve) => { resolveFirst = resolve; });
  const original = api.summary.bind(api);
  vi.spyOn(api, "summary").mockImplementation((id) => id === headphone.id ? first : original(id));
  const view = render(<SummaryDashboard api={api} product={headphone} />);
  view.rerender(<SummaryDashboard api={api} product={speaker} />);
  expect(await screen.findByText(/Illustrative summary for Arc Portable Speaker/)).toBeInTheDocument();
  resolveFirst(await original(headphone.id));
  await waitFor(() => expect(screen.queryByText(/Illustrative summary for Forma Studio Wireless/)).not.toBeInTheDocument());
});

it("validates integer threshold locally and shows role denial", async () => {
  const api = new DemoApi();
  const save = vi.spyOn(api, "summarySettings").mockRejectedValue(new ApiError(403, "request_failed"));
  render(<SummarySettings api={api} product={headphone.id} value={1} onChanged={() => {}} />);
  const field = screen.getByRole("spinbutton", { name: "New reviews before update" });
  fireEvent.change(field, { target: { value: "101" } });
  expect(screen.getByRole("alert")).toHaveTextContent("whole number from 1 to 100");
  expect(save).not.toHaveBeenCalled();
  fireEvent.change(field, { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save threshold" }));
  expect(await screen.findByText(/does not have permission/)).toBeInTheDocument();
});

it("uses the same key after an uncertain refresh response", async () => {
  const api = new DemoApi();
  const base = await api.summary(headphone.id);
  vi.spyOn(api, "summary").mockResolvedValue({ ...base, pending_review_count: 1 });
  const refresh = vi.spyOn(api, "refreshSummary").mockRejectedValueOnce(new ApiError(0, "network")).mockResolvedValueOnce(base);
  render(<SummaryDashboard api={api} product={headphone} />);
  fireEvent.click(await screen.findByRole("button", { name: "Update now" }));
  fireEvent.click(await screen.findByRole("button", { name: "Retry update request" }));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2));
  expect(refresh.mock.calls[0][2]).toBe(refresh.mock.calls[1][2]);
});

it("paginates history and binds evidence and questions to the selected version", async () => {
  const api = new DemoApi();
  for (let i = 0; i < 3; i++) {
    await api.submit(headphone.id, { rating: 3, title: `Review ${i}`, text: `Review text ${i}` }, `key-${i}`);
  }
  const question = vi.spyOn(api, "summaryQuestion");
  render(<SummaryDashboard api={api} product={headphone} />);
  fireEvent.click(await screen.findByRole("button", { name: "View version history" }));
  const history = await screen.findByRole("region", { name: "Summary history" });
  fireEvent.click(await within(history).findByRole("button", { name: "Load older versions" }));
  expect(await within(history).findByRole("button", { name: /Version 1/ })).toBeInTheDocument();
  fireEvent.click(within(history).getByRole("button", { name: /Version 1/ }));
  expect(await screen.findByText("Historical version 1")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Evidence for version 1" })).toHaveTextContent("demo-headphones-review-0");
  expect(screen.queryByText(/Guidance used in this version:/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Question" }), { target: { value: "How is the battery?" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(question).toHaveBeenCalledWith(headphone.id, 1, "How is the battery?"));
});

it("retries the first history request when it fails", async () => {
  const api = new DemoApi();
  const real = api.summaryHistory.bind(api);
  const history = vi.spyOn(api, "summaryHistory")
    .mockRejectedValueOnce(new ApiError(503, "request_failed"))
    .mockImplementation((product, cursor) => real(product, cursor));
  render(<SummaryDashboard api={api} product={headphone} />);
  fireEvent.click(await screen.findByRole("button", { name: "View version history" }));
  const panel = await screen.findByRole("region", { name: "Summary history" });
  fireEvent.click(await within(panel).findByRole("button", { name: "Try again" }));
  await waitFor(() => expect(history).toHaveBeenCalledTimes(2));
  expect(await within(panel).findByRole("button", { name: /Version 1/ })).toBeInTheDocument();
});

it("does not open a newly selected product's history when prior history was open", async () => {
  const api = new DemoApi();
  const history = vi.spyOn(api, "summaryHistory");
  const view = render(<SummaryDashboard api={api} product={headphone} />);
  fireEvent.click(await screen.findByRole("button", { name: "View version history" }));
  await waitFor(() => expect(history).toHaveBeenCalledWith(headphone.id));
  view.rerender(<SummaryDashboard api={api} product={speaker} />);
  expect(await screen.findByText(/Illustrative summary for Arc Portable Speaker/)).toBeInTheDocument();
  expect(history).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "View version history" })).toBeInTheDocument();
});

it("keeps a demo review included in its original version after another publication", async () => {
  const api = new DemoApi();
  const first = await api.submit(headphone.id, { rating: 3, title: "First", text: "First report" }, "first");
  const second = await api.submit(headphone.id, { rating: 4, title: "Second", text: "Second report" }, "second");
  expect((await api.status(first.id)).summary).toEqual({ status: "included", version: 2 });
  expect((await api.status(second.id)).summary).toEqual({ status: "included", version: 3 });
});

it("replays a demo submission with its later published inclusion state", async () => {
  const api = new DemoApi();
  await api.summarySettings(headphone.id, 2);
  const firstBody = { rating: 3, title: "First", text: "First report" };
  const first = await api.submit(headphone.id, firstBody, "first-key");
  expect(first.summary).toEqual({ status: "waiting", version: null });
  await api.submit(headphone.id, { rating: 4, title: "Second", text: "Second report" }, "second-key");
  expect((await api.submit(headphone.id, firstBody, "first-key")).summary).toEqual({ status: "included", version: 2 });
});
