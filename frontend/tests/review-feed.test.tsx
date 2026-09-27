import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DemoApi } from "../src/demo";
import { HttpApi } from "../src/api";
import { ReviewFeed } from "../src/ReviewFeed";
import type { Review } from "../src/types";

function review(id: string, rating: number, source: Review["source"] = "user_submission"): Review {
  return { id, parent_asin: "a", asin: "a", title: `Title ${id}`, text: `Text ${id}`, rating,
    timestamp: "2026-09-28T00:00:00Z", source, batch_id: null, processing: null };
}

it("requests priority feed and filters at the server without confusing it with summary citations", async () => {
  const api = new DemoApi();
  const calls = vi.spyOn(api, "reviews").mockResolvedValue({ items: [review("new-one", 1)], next_cursor: null });
  render(<ReviewFeed api={api} product="a" />);
  expect(await screen.findByText("Text new-one")).toBeInTheDocument();
  expect(screen.getByText("What reviewers said")).toBeInTheDocument();
  expect(screen.getByText("New review")).toBeInTheDocument();
  expect(within(screen.getByText("Title new-one").closest("article")!).getByText("1 star")).toBeInTheDocument();
  expect(calls).toHaveBeenCalledWith("a", undefined, undefined, undefined, { sort: "priority", limit: 5 });
  fireEvent.click(screen.getByRole("button", { name: "Negative" }));
  await waitFor(() => expect(calls).toHaveBeenLastCalledWith("a", undefined, undefined, undefined,
    { sort: "priority", sentiment: "negative", limit: 5 }));
  fireEvent.change(screen.getByLabelText("Rating"), { target: { value: "1" } });
  await waitFor(() => expect(calls).toHaveBeenLastCalledWith("a", undefined, undefined, undefined,
    { sort: "priority", sentiment: "negative", rating: 1, limit: 5 }));
  fireEvent.change(screen.getByLabelText("Review order"), { target: { value: "newest" } });
  await waitFor(() => expect(calls).toHaveBeenLastCalledWith("a", undefined, undefined, undefined,
    { sort: "newest", sentiment: "negative", rating: 1, limit: 5 }));
});

it("loads more and refreshes loaded pages when new reviews arrive", async () => {
  const api = new DemoApi();
  const calls = vi.spyOn(api, "reviews").mockImplementation(async (_p, _s, _b, cursor) => cursor
    ? { items: Array.from({ length: 5 }, (_, i) => review(`old-${i}`, 5)), next_cursor: null }
    : { items: Array.from({ length: 5 }, (_, i) => review(`new-${i}`, 1)), next_cursor: "next" });
  const view = render(<ReviewFeed api={api} product="a" reviewVersion={0} />);
  expect(await screen.findByText("Text new-0")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Product reviews" }).querySelectorAll(".review-feed-card")).toHaveLength(5);
  fireEvent.click(screen.getByRole("button", { name: "Load more reviews" }));
  expect(await screen.findByText("Text old-0")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Product reviews" }).querySelectorAll(".review-feed-card")).toHaveLength(10);
  view.rerender(<ReviewFeed api={api} product="a" reviewVersion={1} />);
  await waitFor(() => expect(calls).toHaveBeenCalledWith("a", undefined, undefined, "next", { sort: "priority", limit: 5 }));
});

it("does not show a prior product response after selection changes", async () => {
  const api = new DemoApi();
  let release!: (value: { items: Review[]; next_cursor: null }) => void;
  const slow = new Promise<{ items: Review[]; next_cursor: null }>((resolve) => { release = resolve; });
  vi.spyOn(api, "reviews").mockImplementation((product) => product === "a" ? slow : Promise.resolve({ items: [review("b", 4)], next_cursor: null }));
  const view = render(<ReviewFeed api={api} product="a" />);
  view.rerender(<ReviewFeed api={api} product="b" />);
  expect(await screen.findByText("Text b")).toBeInTheDocument();
  await act(async () => { release({ items: [review("a", 1)], next_cursor: null }); });
  expect(screen.queryByText("Text a")).not.toBeInTheDocument();
});

it("checks the selected live product again after five seconds", async () => {
  vi.useFakeTimers();
  try {
    const api = new DemoApi();
    Object.defineProperty(api, "demo", { value: false });
    const calls = vi.spyOn(api, "reviews").mockResolvedValueOnce({ items: [review("first", 2)], next_cursor: null })
      .mockResolvedValue({ items: [review("second", 1)], next_cursor: null });
    render(<ReviewFeed api={api} product="a" />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("Text first")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText("Text second")).toBeInTheDocument();
    expect(calls).toHaveBeenCalledTimes(2);
  } finally { vi.useRealTimers(); }
});

it("uses explicit review-list filters only for the feed request", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], next_cursor: null })));
  await new HttpApi("token", fetcher).reviews("a/b", undefined, undefined, "cursor/id", {
    sort: "priority", sentiment: "negative", rating: 1, limit: 5,
  });
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/products/a%2Fb/reviews?limit=5&cursor=cursor%2Fid&sentiment=negative&rating=1&sort=priority");
});
