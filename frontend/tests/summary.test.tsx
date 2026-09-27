import { expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  vi.spyOn(api, "summary").mockResolvedValue({ product_id: headphone.id, current: null, last_updated_at: null, update_threshold: 1,
    pending_review_count: 0, status: "uninitialized", error_code: null, memory_status: "pending" });
  render(<SummaryDashboard api={api} product={headphone} />);
  expect(await screen.findByText("No published summary yet.")).toBeInTheDocument();
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

it("keeps a demo review included in its original version after another publication", async () => {
  const api = new DemoApi();
  const first = await api.submit(headphone.id, { rating: 3, title: "First", text: "First report" }, "first");
  const second = await api.submit(headphone.id, { rating: 4, title: "Second", text: "Second report" }, "second");
  expect((await api.status(first.id)).summary).toEqual({ status: "included", version: 2 });
  expect((await api.status(second.id)).summary).toEqual({ status: "included", version: 3 });
});
