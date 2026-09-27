import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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
