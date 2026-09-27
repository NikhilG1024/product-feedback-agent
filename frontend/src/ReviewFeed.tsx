import { useEffect, useRef, useState } from "react";
import type { Api, Review, ReviewListOptions } from "./types";
import { errorMessage } from "./api";
import { ErrorNotice, Loading } from "./ui";

type Sentiment = ReviewListOptions["sentiment"] | "all";
const sentiments: { value: Sentiment; label: string }[] = [
  { value: "all", label: "All" }, { value: "positive", label: "Positive" },
  { value: "neutral", label: "Neutral" }, { value: "negative", label: "Negative" },
];
export function ReviewFeed({ api, product, reviewVersion = 0, active = true, revision }: { api: Api; product: string; reviewVersion?: number; active?: boolean; revision?: string }) {
  const [sentiment, setSentiment] = useState<Sentiment>("all");
  const [rating, setRating] = useState<ReviewListOptions["rating"]>();
  const [sort, setSort] = useState<"priority" | "newest">("priority");
  const [pageCount, setPageCount] = useState(1);
  const [items, setItems] = useState<Review[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [revisionTick, setRevisionTick] = useState(0);
  const lastRevision = useRef(revision);
  const generation = useRef(0);

  useEffect(() => {
    if (revision === undefined) return;
    if (lastRevision.current === undefined) { lastRevision.current = revision; return; }
    if (lastRevision.current !== revision) {
      lastRevision.current = revision;
      setRevisionTick((n) => n + 1);
    }
  }, [revision]);

  useEffect(() => {
    setSentiment("all"); setRating(undefined); setSort("priority"); setPageCount(1);
    setItems([]); setNextCursor(null); setLoading(true); setError("");
  }, [product]);

  useEffect(() => {
    if (!active) return;
    const currentGeneration = ++generation.current;
    let cancelled = false;
    const options: ReviewListOptions = { sort, limit: 5 };
    if (sentiment !== "all") options.sentiment = sentiment;
    if (rating) options.rating = rating;
    async function load() {
      try {
        const rows: Review[] = [];
        let cursor: string | null = null;
        for (let pageIndex = 0; pageIndex < pageCount; pageIndex++) {
          const page = await api.reviews(product, undefined, undefined, cursor ?? undefined, options);
          if (cancelled) return;
          rows.push(...page.items);
          cursor = page.next_cursor;
          if (!cursor) break;
        }
        if (!cancelled) {
          setItems(rows); setNextCursor(cursor); setError("");
        }
      } catch (e) {
        if (!cancelled) setError(errorMessage(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => { cancelled = true; if (generation.current === currentGeneration) generation.current++; };
  }, [api, product, sentiment, rating, sort, reviewVersion, revisionTick, retry, active]);

  async function loadMore() {
    if (!nextCursor || loading) return;
    const currentGeneration = generation.current;
    const options: ReviewListOptions = { sort, limit: 5 };
    if (sentiment !== "all") options.sentiment = sentiment;
    if (rating) options.rating = rating;
    setLoading(true);
    try {
      const page = await api.reviews(product, undefined, undefined, nextCursor, options);
      if (generation.current !== currentGeneration) return;
      setItems((previous) => [...previous, ...page.items]);
      setNextCursor(page.next_cursor);
      setPageCount((count) => count + 1);
      setError("");
    } catch (e) {
      if (generation.current === currentGeneration) setError(errorMessage(e));
    } finally {
      if (generation.current === currentGeneration) setLoading(false);
    }
  }

  function resetFilters(next: { sentiment?: Sentiment; rating?: ReviewListOptions["rating"]; sort?: "priority" | "newest" }) {
    if (next.sentiment !== undefined) setSentiment(next.sentiment);
    if ("rating" in next) setRating(next.rating);
    if (next.sort) setSort(next.sort);
    setPageCount(1); setItems([]); setNextCursor(null); setLoading(true); setError("");
  }

  return <section className="review-feed" aria-label="Product reviews">
    <div className="section-heading"><div><h2>What reviewers said</h2><p className="fine-print">Actual product reviews, including new app submissions before they appear in a published summary.</p></div></div>
    <div className="review-feed-controls">
      <div className="review-sentiments" role="group" aria-label="Sentiment based on star rating">
        {sentiments.map((choice) => <button key={choice.value} type="button" className={`button ${sentiment === choice.value ? "active" : ""}`}
          aria-pressed={sentiment === choice.value} onClick={() => resetFilters({ sentiment: choice.value })}>{choice.label}</button>)}
      </div>
      <label>Rating <select aria-label="Rating" value={rating ?? ""} onChange={(e) => resetFilters({ rating: e.target.value ? Number(e.target.value) as ReviewListOptions["rating"] : undefined })}>
        <option value="">All stars</option>{[1, 2, 3, 4, 5].map((value) => <option value={value} key={value}>{value} star{value === 1 ? "" : "s"}</option>)}
      </select></label>
      <label>Order <select aria-label="Review order" value={sort} onChange={(e) => resetFilters({ sort: e.target.value as "priority" | "newest" })}>
        <option value="priority">New &amp; negative first</option><option value="newest">Newest first</option>
      </select></label>
    </div>
    <p className="fine-print">Sentiment groups are based on stars: negative 1–2, neutral 3, positive 4–5. “New &amp; negative first” shows app submissions first, then 1–2-star reviews within each source.</p>
    {loading && <Loading>Loading reviews…</Loading>}
    {error && <ErrorNotice message={error} retry={() => setRetry((n) => n + 1)} />}
    {!loading && !error && items.length === 0 && <p className="muted">No reviews match these filters.</p>}
    {items.length > 0 && <div className="review-feed-list">{items.map((review) => <article className="review-feed-card" key={review.id}>
      <div className="review-feed-meta"><span className="pill">{review.rating} star{review.rating === 1 ? "" : "s"}</span>
        <span>{review.source === "user_submission" ? "New review" : "Historical review"}</span>
        <time dateTime={review.timestamp}>{new Date(review.timestamp).toLocaleString()}</time></div>
      <h3>{review.title}</h3><p>{review.text}</p>
    </article>)}</div>}
    {nextCursor && <button className="button" disabled={loading} onClick={() => void loadMore()}>Load more reviews</button>}
  </section>;
}
