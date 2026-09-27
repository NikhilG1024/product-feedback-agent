import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, Star } from "lucide-react";
import type { Api, Product, ReviewInput, Submission } from "./types";
import { ApiError, errorMessage } from "./api";
import { ErrorNotice, isFinished, StatePill } from "./ui";
export function Reviewer({
  api,
  product,
  onSaved,
}: {
  api: Api;
  product: Product;
  onSaved: () => void;
}) {
  const [rating, setRating] = useState(0),
    [title, setTitle] = useState(""),
    [text, setText] = useState(""),
    [busy, setBusy] = useState(false),
    [rejected, setRejected] = useState(false),
    [error, setError] = useState(""),
    [result, setResult] = useState<Submission | null>(null),
    [statusError, setStatusError] = useState("");
  const attempt = useRef<{ key: string; body: ReviewInput } | null>(null);
  const alive = useRef(true);
  const success = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    if (result) success.current?.focus();
  }, [result?.id]);
  async function refresh() {
    if (!result) return;
    try {
      const next = await api.status(result.id);
      if (alive.current) {
        setResult(next);
        setStatusError("");
      }
    } catch (e) {
      if (alive.current) setStatusError(errorMessage(e));
    }
  }
  useEffect(() => {
    if (
      !result ||
      api.demo ||
      isFinished(result.processing?.status) ||
      statusError
    )
      return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api.status(result!.id);
        if (cancelled) return;
        setResult(next);
        if (!isFinished(next.processing?.status))
          timer = setTimeout(poll, 4000);
      } catch (e) {
        if (!cancelled) setStatusError(errorMessage(e));
      }
    }
    timer = setTimeout(poll, 4000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [result?.id, api, statusError]);
  async function submit() {
    if (busy) return;
    if (!attempt.current) {
      if (!rating || !title.trim() || !text.trim()) {
        setError("Choose a rating and add a title and review.");
        return;
      }
      attempt.current = {
        key: crypto.randomUUID(),
        body: { rating, title, text },
      };
    }
    setBusy(true);
    setRejected(false);
    setError("");
    try {
      const saved = await api.submit(
        product.id,
        attempt.current.body,
        attempt.current.key,
      );
      if (alive.current) {
        setResult(saved);
        onSaved();
      }
    } catch (e) {
      if (alive.current) {
        setError(errorMessage(e));
        setRejected(
          e instanceof ApiError &&
            [400, 401, 403, 404, 413, 422, 429].includes(e.status),
        );
      }
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <section className="reviewer">
      <div className="heading">
        <h1>How was your experience?</h1>
        <p>A few details can help make the product better.</p>
      </div>
      <div className="review-card">
        <div className="review-product">
          <span className="category">{product.product_type || "Product"}</span>
          <h2>{product.title}</h2>
        </div>
        {result ? (
          <>
            <div className="saved-icon">
              <Check />
            </div>
            <h2 ref={success} tabIndex={-1}>
              Your review is saved.
            </h2>
            <p className="muted">
              {api.demo
                ? "Sample mode: no feedback was sent to a server."
                : "You can leave this page. Processing continues on the server."}
            </p>
            <div className="status-row">
              <span>Your review</span>
              <span className="pill ready">Saved</span>
            </div>
            <div className="status-row">
              <span>Preparing feedback for the AI</span>
              <StatePill state={result.processing?.memory_status} />
            </div>
            <div className="status-row">
              <span>Finding issues in your review</span>
              <StatePill state={result.processing?.classification_status} />
            </div>
            <p className="fine-print">
              Saving a review does not mean AI processing is finished. Reviews
              cannot be edited.
            </p>
            {result.processing?.status === "failed" && (
              <ErrorNotice message="Your review is saved, but AI processing could not finish. Ask the demo host to check the worker." />
            )}
            {statusError && (
              <ErrorNotice message={statusError} retry={refresh} />
            )}
            <button className="button" onClick={refresh}>
              Check progress
            </button>
          </>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <fieldset
              disabled={busy || !!attempt.current}
              className="review-fields"
            >
              <fieldset className="field rating">
                <legend>Overall rating</legend>
                <div
                  className="stars"
                  role="radiogroup"
                  aria-label="Overall rating"
                >
                  {[1, 2, 3, 4, 5].map((n) => (
                    <label key={n} className={n <= rating ? "selected" : ""}>
                      <input
                        type="radio"
                        name="rating"
                        value={n}
                        checked={rating === n}
                        onChange={() => setRating(n)}
                        aria-label={`${n} ${n === 1 ? "star" : "stars"}`}
                        required
                      />
                      <Star
                        fill={n <= rating ? "currentColor" : "none"}
                        aria-hidden="true"
                      />
                    </label>
                  ))}
                </div>
                <small>
                  {rating ? `${rating} out of 5` : "Choose a rating"}
                </small>
              </fieldset>
              <label className="field">
                Review title
                <input
                  maxLength={200}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  required
                  placeholder="A short summary of your experience"
                />
              </label>
              <label className="field">
                Your experience
                <textarea
                  aria-label="Your experience"
                  maxLength={10000}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  required
                  placeholder="What worked? What could be better?"
                  rows={5}
                />
                <small>
                  {text.length.toLocaleString()} / 10,000 characters
                </small>
              </label>
            </fieldset>
            {error && <ErrorNotice message={error} />}
            {rejected && (
              <button
                type="button"
                className="text-button"
                disabled={busy}
                onClick={() => {
                  if (busy) return;
                  attempt.current = null;
                  setRejected(false);
                  setError("");
                }}
              >
                Correct draft
              </button>
            )}
            <div className="submit-row">
              <p className="fine-print">
                Leave out personal information.
                <br />
                Reviews cannot be edited after submission.
              </p>
              <button className="button primary" type="submit" disabled={busy}>
                {busy
                  ? "Saving…"
                  : attempt.current
                    ? "Retry submission"
                    : "Submit review"}
                <ArrowRight size={15} />
              </button>
            </div>
            {attempt.current && error && (
              <p className="fine-print">
                Your feedback is locked for a safe retry. Retrying will not
                create a duplicate review.
              </p>
            )}
          </form>
        )}
      </div>
    </section>
  );
}
