import { describe, it, expect, vi } from "vitest";
import { HttpApi, ApiError } from "../src/api";
import { DemoApi } from "../src/demo";

describe("HTTP boundary", () => {
  it("calls the browser transport without binding it to the API instance", async () => {
    const fetcher = vi.fn(function (this: unknown) {
      expect(this).toBeUndefined();
      return Promise.resolve(
        new Response(JSON.stringify({ items: [], next_cursor: null })),
      );
    });
    await new HttpApi("fixture-token", fetcher).products();
  });

  it("uses server token and versioned routes, never role headers", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ items: [], next_cursor: null })),
      );
    const api = new HttpApi("secret-demo-token", fetcher);
    await api.products("next/id");
    expect(fetcher.mock.calls[0][0]).toBe(
      "/api/v1/products?limit=30&cursor=next%2Fid",
    );
    expect(fetcher.mock.calls[0][1].headers).toMatchObject({
      Authorization: "Bearer secret-demo-token",
    });
    expect(fetcher.mock.calls[0][1].headers).not.toHaveProperty("X-Role");
  });
  it("preserves submission key and exact payload for retry", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ id: "r1", processing: null })),
      );
    const api = new HttpApi("token", fetcher);
    const body = { title: "Good", text: "Useful product", rating: 4 };
    await api.submit("product/a", body, "stable-key");
    expect(fetcher.mock.calls[0][0]).toBe(
      "/api/v1/products/product%2Fa/reviews",
    );
    expect(fetcher.mock.calls[0][1].headers["Idempotency-Key"]).toBe(
      "stable-key",
    );
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(body);
  });
  it("discards unsafe server text and explains 403", async () => {
    const api = new HttpApi(
      "token",
      vi
        .fn()
        .mockResolvedValue(
          new Response("sensitive internal text", { status: 403 }),
        ),
    );
    await expect(api.products()).rejects.toMatchObject({
      status: 403,
      message: expect.stringContaining("permission"),
    });
    await expect(api.products()).rejects.not.toThrow("sensitive");
  });
  it("reports narrower scope without exposing raw error bodies", async () => {
    const api = new HttpApi(
      "token",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ error: { code: "narrower_scope_required" } }),
            { status: 422 },
          ),
        ),
    );
    await expect(api.products()).rejects.toThrow("smaller");
  });
});
describe("demo behavior", () => {
  it("only publishes results when complete and keeps baseline free of guidance", async () => {
    const api = new DemoApi();
    const products = await api.products();
    const p = products.items[0];
    const queued = await api.analyze(p.id, {
      mode: "baseline",
      scope: { source: "amazon_2023", batch_id: "demo:B", evaluation: false },
    });
    expect(queued.summary).toBeNull();
    expect(queued.status).toBe("pending");
    const complete = await api.run(queued.id);
    expect(complete.status).toBe("completed");
    expect(complete.guidance_references).toEqual([]);
    expect(complete.parent_asin).toBe(p.id);
  });
  it("rejects cross-product evidence access", async () => {
    const api = new DemoApi();
    const p = (await api.products()).items;
    const r = await api.analyze(p[0].id, {
      mode: "memory",
      scope: { source: "amazon_2023", batch_id: "demo:B", evaluation: false },
    });
    await api.run(r.id);
    await expect(api.findings(p[1].id, r.id)).rejects.toBeInstanceOf(ApiError);
  });
  it("replays identical submissions and rejects changed payload with same key", async () => {
    const api = new DemoApi();
    const p = (await api.products()).items[0];
    const body = { title: "Review", text: "Real description", rating: 3 };
    const one = await api.submit(p.id, body, "key");
    const two = await api.submit(p.id, body, "key");
    expect(one.id).toBe(two.id);
    await expect(
      api.submit(p.id, { ...body, rating: 4 }, "key"),
    ).rejects.toMatchObject({ status: 409 });
  });
});

it("asks a question against one completed report", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        answer: "Not enough evidence.",
        evidence: [],
        insufficient_evidence: true,
      }),
    ),
  );
  const response = await new HttpApi("fixture", fetcher).question(
    "product/a",
    "run-1",
    "What supports this?",
  );
  expect(fetcher.mock.calls[0][0]).toBe(
    "/api/v1/products/product%2Fa/questions",
  );
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    run_id: "run-1",
    question: "What supports this?",
  });
  expect(response.insufficient_evidence).toBe(true);
});

it.each([
  ["cutoff_after_batch_end", 422, "end of this review group"],
  ["historical_batch_required", 422, "Choose a review group"],
  ["legacy_analysis_unsupported", 409, "new analysis"],
  ["model_input_too_large", 422, "model can accept"],
])("explains backend scope/budget error %s", async (code, status, message) => {
  const api = new HttpApi(
    "fixture",
    vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ error: { code } }), { status }),
      ),
  );
  await expect(api.products()).rejects.toThrow(message);
});
