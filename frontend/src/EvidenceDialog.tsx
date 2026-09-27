import { useEffect, useState } from "react";
import type { Api, Finding, Product, Review, Run } from "./types";
import { errorMessage } from "./api";
import { batchLabel, dateLabel, ErrorNotice, Loading, Modal } from "./ui";
function OriginalReview({
  api,
  product,
  run,
  id,
}: {
  api: Api;
  product: Product;
  run: Run;
  id: string;
}) {
  const [review, setReview] = useState<Review | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function load() {
    setBusy(true);
    setError("");
    try {
      let cursor: string | undefined;
      const seen = new Set<string>();
      do {
        const page = await api.reviews(
          product.id,
          run.scope.source,
          run.scope.batch_id,
          cursor,
        );
        const found = page.items.find((r) => r.id === id);
        if (found) {
          setReview(found);
          return;
        }
        cursor = page.next_cursor || undefined;
        if (cursor && seen.has(cursor)) break;
        if (cursor) seen.add(cursor);
      } while (cursor);
      setError(
        "The full review is not available. The quoted evidence above belongs to this completed analysis.",
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="original-review">
      {review ? (
        <>
          <h4>{review.title}</h4>
          <p className="review-meta">
            {review.rating} / 5 · {dateLabel(review.timestamp)} ·{" "}
            {review.source === "amazon_2023" ? "Amazon review" : "App review"}
          </p>
          <p>{review.text}</p>
        </>
      ) : (
        <button className="text-button" disabled={busy} onClick={load}>
          {busy ? "Loading review…" : "Read full review"}
        </button>
      )}
      {error && <ErrorNotice message={error} />}
    </div>
  );
}
export function EvidenceDialog({
  api,
  product,
  run,
  findingId,
  onClose,
  onGuidance,
}: {
  api: Api;
  product: Product;
  run: Run;
  findingId: string;
  onClose: () => void;
  onGuidance: (ids: string[]) => void;
}) {
  const [finding, setFinding] = useState<Finding | null>(null),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError("");
    api
      .findings(product.id, run.id)
      .then((data) => {
        if (cancelled) return;
        const f = data.items.find((f) => f.id === findingId);
        if (f) setFinding(f);
        else
          setError("This issue is no longer available in the selected report.");
      })
      .catch((e) => {
        if (!cancelled) setError(errorMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, product.id, run.id, findingId, retry]);
  return (
    <Modal
      title={finding?.theme || "What customers said"}
      onClose={onClose}
      wide
    >
      {error ? (
        <ErrorNotice message={error} retry={() => setRetry((n) => n + 1)} />
      ) : !finding ? (
        <Loading>Loading supporting reviews…</Loading>
      ) : (
        <>
          <div className="chips">
            <span className="pill">
              {run.scope.source === "amazon_2023"
                ? "Past Amazon reviews"
                : "New app reviews"}
            </span>
            {run.scope.batch_id && (
              <span className="pill">{batchLabel(run.scope.batch_id)}</span>
            )}
            <span className="pill">
              {finding.supporting_review_count} of {run.denominator} reviews
            </span>
          </div>
          <p className="muted">{finding.description}</p>
          <h3>What customers said</h3>
          {finding.evidence.map((e, i) => (
            <article className="evidence-card" key={e.review_id + ":" + i}>
              <blockquote>“{e.quote}”</blockquote>
              <span className="review-id">Review {e.review_id}</span>
              <OriginalReview
                api={api}
                product={product}
                run={run}
                id={e.review_id}
              />
            </article>
          ))}
          <p className="fine-print">
            {finding.evidence_sampled
              ? "A selection of supporting quotes is shown. "
              : ""}
            Quotes support where the feedback came from, not a confirmed
            technical cause.
          </p>
          <button
            className="button primary"
            onClick={() => onGuidance(finding.evidence.map((e) => e.review_id))}
          >
            Add guidance about this issue
          </button>
        </>
      )}
    </Modal>
  );
}
