import { expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { InitializationProgress } from "../src/InitializationProgress";
import { DemoApi } from "../src/demo";

it("shows live citation checks without calling them published summaries", async () => {
  const api = new DemoApi();
  Object.defineProperty(api, "demo", { value: false });
  vi.spyOn(api, "summaryProgress").mockResolvedValue({
    availability: "available",
    progress: {
      run_id: "run-a", started_at: "2026-09-27T12:00:00Z",
      updated_at: "2026-09-27T12:01:00Z", status: "running",
      total: 2, workers: 1, completed: 1, failed: 0, active: 1,
      queued: 0, published: null, next_offset: null,
      products: [{ id: "B1", title: "One", status: "citation_checks_passed" },
        { id: "B2", title: "Two", status: "generating" }],
    },
  });
  render(<InitializationProgress api={api} />);
  expect(await screen.findByText(/1 citation-checked/)).toBeInTheDocument();
  expect(screen.getByText(/Published summaries: unknown/)).toBeInTheDocument();
  expect(screen.getByText(/One · Citation checks passed/)).toBeInTheDocument();
});

it("labels sample telemetry as unavailable", async () => {
  render(<InitializationProgress api={new DemoApi()} />);
  expect(await screen.findByText(/Live initialization progress is unavailable in sample mode/)).toBeInTheDocument();
});

it("loads paused progress once without idle polling and supports manual refresh", async () => {
  vi.useFakeTimers();
  try {
  const api = new DemoApi();
  Object.defineProperty(api, "demo", { value: false });
  const poll = vi.spyOn(api, "summaryProgress").mockResolvedValue({
    availability: "available",
    progress: {
      run_id: "run-a", started_at: "2026-09-27T12:00:00Z",
      updated_at: "2026-09-27T12:06:00Z", status: "paused_quality_review",
      pause_reason: "untrusted internal detail", total: 12, workers: 6,
      completed: 6, failed: 0, active: 0, queued: 6, published: null,
      next_offset: null, products: [],
    },
  });
  await act(async () => { render(<InitializationProgress api={api} />); });
  expect(screen.getByText(/Initialization is paused/)).toBeInTheDocument();
  expect(screen.queryByText(/untrusted internal detail/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Estimated local finish/)).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(poll).toHaveBeenCalledTimes(1);
  await act(async () => { fireEvent.click(screen.getByRole("button", {name: "Refresh progress"})); });
  expect(poll).toHaveBeenCalledTimes(2);
  } finally { vi.useRealTimers(); }
});

it("drops an old page when manual refresh switches to a new run", async () => {
  vi.useFakeTimers();
  try {
    const api = new DemoApi();
    Object.defineProperty(api, "demo", { value: false });
    let resolveOldPage!: (value: Awaited<ReturnType<typeof api.summaryProgress>>) => void;
    const oldPage = new Promise<Awaited<ReturnType<typeof api.summaryProgress>>>((resolve) => {
      resolveOldPage = resolve;
    });
    const response = (run: string, id: string, nextOffset: number | null) => ({
      availability: "available" as const,
      progress: {
        run_id: run, started_at: "2026-09-27T12:00:00Z",
        updated_at: "2026-09-27T12:01:00Z", status: "running" as const,
        total: 2, workers: 1, completed: 0, failed: 0, active: 1,
        queued: 1, published: null, next_offset: nextOffset,
        products: [{ id, title: id, status: "generating" as const }],
      },
    });
    const request = vi.spyOn(api, "summaryProgress")
      .mockResolvedValueOnce(response("A", "A1", 1))
      .mockReturnValueOnce(oldPage)
      .mockResolvedValueOnce(response("B", "B1", null));
    await act(async () => { render(<InitializationProgress api={api} />); });
    fireEvent.click(screen.getByRole("button", { name: "Show more products" }));
    await act(async () => { fireEvent.click(screen.getByRole("button", {name: "Refresh progress"})); });
    expect(screen.getByText(/B1 · Generating/)).toBeInTheDocument();
    await act(async () => { resolveOldPage(response("A", "A2", null)); });
    expect(request).toHaveBeenCalledTimes(3);
    expect(screen.getByText(/B1 · Generating/)).toBeInTheDocument();
    expect(screen.queryByText(/A2 · Generating/)).not.toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

it.each([
  ["running", 5, 0, "available", /Estimating finish after 6 products/],
  ["running", 5, 1, "available", /Approx\. 6 min remaining/],
  ["running", 5, 1, "stale", /Estimate unavailable until progress updates/],
  ["completed", 12, 0, "available", /Initialization finished/],
] as const)("shows bounded finish estimate for %s with %i completed, %i failed, %s telemetry", async (status, completed, failed, availability, expected) => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-27T12:06:00Z"));
  try {
    const api = new DemoApi();
    Object.defineProperty(api, "demo", { value: false });
    vi.spyOn(api, "summaryProgress").mockResolvedValue({
      availability,
      progress: {
        run_id: "run-a", started_at: "2026-09-27T12:00:00Z",
        updated_at: "2026-09-27T12:06:00Z", status,
        total: 12, workers: 6, completed, failed,
        active: status === "running" ? 6 : 0,
        queued: status === "running" ? 12 - completed - failed - 6 : 0,
        published: null, next_offset: null, products: [],
      },
    });
    let view!: ReturnType<typeof render>;
    await act(async () => { view = render(<InitializationProgress api={api} />); });
    expect(screen.getByText(expected)).toBeInTheDocument();
    if (status === "running") expect(screen.getByText(availability === "stale" ? "Elapsed since start (progress stale): 6 min" : "Elapsed: 6 min")).toBeInTheDocument();
    if (completed + failed === 6 && availability === "available") {
      const localFinish = new Date("2026-09-27T12:12:00Z").toLocaleString();
      expect(screen.getByText(`Estimated local finish: ${localFinish}`)).toBeInTheDocument();
    }
    if (availability === "stale") expect(screen.queryByText(/Estimated local finish:/)).not.toBeInTheDocument();
    view.unmount();
  } finally {
    vi.useRealTimers();
  }
});

it("does not count reused products toward throughput estimate", async () => {
  const api = new DemoApi();
  Object.defineProperty(api, "demo", { value: false });
  vi.spyOn(api, "summaryProgress").mockResolvedValue({ availability: "available", progress: {
    run_id: "reuse", started_at: "2026-09-27T12:00:00Z", updated_at: "2026-09-27T12:06:00Z",
    status: "running", total: 100, workers: 6, completed: 60, reused: 56, failed: 0,
    active: 6, queued: 34, published: null, next_offset: null, products: [],
  }});
  render(<InitializationProgress api={api} />);
  expect(await screen.findByText(/Estimating finish after 6 products/)).toBeInTheDocument();
  expect(screen.queryByText(/Approx\./)).not.toBeInTheDocument();
});
