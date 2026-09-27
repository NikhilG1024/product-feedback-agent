import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { localAuthState, proxyCredentialFor, safeLocalTarget } from "../localDevAuth";
import App from "../src/App";
import { HttpApi } from "../src/api";

const credentials = { DEMO_PM_TOKEN: "pm-secret", DEMO_REVIEWER_TOKEN: "reviewer-secret", LOCAL_DEMO_AUTH: "1" };

it("enables auto auth only for the loopback dev server and exact local API target", () => {
  expect(safeLocalTarget("http://127.0.0.1:8000")).toBe(true);
  expect(safeLocalTarget("http://localhost:8000")).toBe(true);
  for (const target of ["https://127.0.0.1:8000", "http://127.0.0.1:8001", "http://127.0.0.1:8000/other", "http://evil.example:8000", "http://localhost.evil:8000"])
    expect(safeLocalTarget(target)).toBe(false);
  expect(localAuthState("serve", "http://127.0.0.1:8000", credentials).enabled).toBe(true);
  expect(localAuthState("build", "http://127.0.0.1:8000", credentials).enabled).toBe(false);
  expect(localAuthState("serve", "http://evil.example:8000", credentials).enabled).toBe(false);
  expect(localAuthState("serve", "http://127.0.0.1:8000", { ...credentials, LOCAL_DEMO_AUTH: "0" }).enabled).toBe(false);
});

it("routes reviewer writes/status to reviewer auth and all other API calls to PM auth", () => {
  const state = localAuthState("serve", "http://127.0.0.1:8000", credentials);
  expect(proxyCredentialFor(state, "127.0.0.1", "POST", "/api/v1/products/P/reviews")).toBe("reviewer-secret");
  expect(proxyCredentialFor(state, "::1", "GET", "/api/v1/reviews/r1/status")).toBe("reviewer-secret");
  expect(proxyCredentialFor(state, "::1", "GET", "/api/v1/reviews/r1/events")).toBe("reviewer-secret");
  expect(proxyCredentialFor(state, "::1", "GET", "/api/v1/products/P/events")).toBe("pm-secret");
  expect(proxyCredentialFor(state, "127.0.0.1", "GET", "/api/v1/products/P/summary")).toBe("pm-secret");
  expect(proxyCredentialFor(state, "127.0.0.1", "PATCH", "/api/v1/products/P/summary/settings")).toBe("pm-secret");
  expect(proxyCredentialFor(state, "192.0.2.1", "POST", "/api/v1/products/P/reviews")).toBeNull();
});

it("sends no bearer credential from browser code in local auto mode", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 }));
  await new HttpApi("", fetcher, true).products();
  const options = fetcher.mock.calls[0][1] as RequestInit;
  expect(options.headers).not.toHaveProperty("Authorization");
  expect(fetcher.mock.calls[0][0]).toMatch(/^\/api\/v1\/products/);
});

it("opens the live workspace without a token prompt when local auth is ready", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url === "/__local_demo_auth") return new Response(JSON.stringify({ enabled: true }), { status: 200 });
    if (url.startsWith("/api/v1/products?")) return new Response(JSON.stringify({ items: [{ id: "P", title: "Product", product_type: null }], next_cursor: null }), { status: 200 });
    if (url.endsWith("/events")) return new Response("event: summary\ndata: " + JSON.stringify({ product_id: "P", current: null, last_updated_at: null,
      update_threshold: 1, pending_review_count: 0, status: "uninitialized", error_code: null,
      memory_status: "unknown" }) + "\n\nevent: reviews_changed\ndata: {\"revision\":\"none\"}\n\n",
      { status: 200, headers: { "Content-Type": "text/event-stream" } });
    return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 });
  });
  render(<App />);
  expect(await screen.findByText(/CONNECTED · Live workspace data/)).toBeInTheDocument();
  expect(await screen.findByText("No published summary yet.")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Write a review" })).toHaveLength(2);
  expect(screen.queryByLabelText("Access token")).not.toBeInTheDocument();
  fetcher.mockRestore();
});

it("shows a connection error instead of silently switching to sample data", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ enabled: true }), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockRejectedValueOnce(new Error("offline"));
  render(<App />);
  expect(await screen.findByText(/Cannot reach the server/)).toBeInTheDocument();
  expect(screen.queryByText(/SAMPLE MODE/)).not.toBeInTheDocument();
  fetcher.mockRestore();
});
