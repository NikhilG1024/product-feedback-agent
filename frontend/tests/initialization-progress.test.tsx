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

it("drops an old page when polling switches to a new run", async () => {
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
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
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
  ["running", 5, 0, /Estimating finish after 6 products/],
  ["running", 5, 1, /Approx\. 6 min remaining/],
  ["completed", 12, 0, /Initialization finished/],
] as const)("shows bounded finish estimate for %s with %i completed and %i failed", async (status, completed, failed, expected) => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-27T12:06:00Z"));
  try {
    const api = new DemoApi();
    Object.defineProperty(api, "demo", { value: false });
    vi.spyOn(api, "summaryProgress").mockResolvedValue({
      availability: "available",
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
    if (status === "running") expect(screen.getByText("Elapsed: 6 min")).toBeInTheDocument();
    if (completed + failed === 6) {
      const localFinish = new Date("2026-09-27T12:12:00Z").toLocaleString();
      expect(screen.getByText(`Estimated local finish: ${localFinish}`)).toBeInTheDocument();
    }
    view.unmount();
  } finally {
    vi.useRealTimers();
  }
});
