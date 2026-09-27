import { useEffect, useState } from "react";
import type { Api, Mode, Product, Run, Source } from "./types";
import { uncertainWrite, errorMessage } from "./api";
import { batchLabel, ErrorNotice, Loading, Modal } from "./ui";
export function AnalysisDialog({
  api,
  product,
  onClose,
  onQueued,
}: {
  api: Api;
  product: Product;
  onClose: () => void;
  onQueued: (r: Run) => void;
}) {
  const [mode, setMode] = useState<Mode>("memory"),
    [demoScope, setDemoScope] = useState(true),
    [source, setSource] = useState<Source>("amazon_2023"),
    [batch, setBatch] = useState(""),
    [batches, setBatches] = useState<string[]>([]),
    [date, setDate] = useState(""),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [uncertain, setUncertain] = useState(false),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    async function discover() {
      try {
        const ids = new Set<string>();
        let cursor: string | undefined;
        const seen = new Set<string>();
        do {
          const page = await api.reviews(
            product.id,
            "amazon_2023",
            undefined,
            cursor,
          );
          if (cancelled) return;
          page.items.forEach((r) => {
            if (r.batch_id && !/(^|:)C$/.test(r.batch_id)) ids.add(r.batch_id);
          });
          cursor = page.next_cursor || undefined;
          if (cursor && seen.has(cursor)) throw Error("Repeated cursor");
          if (cursor) seen.add(cursor);
        } while (cursor);
        const list = [...ids].sort();
        setBatches(list);
        setBatch(list[0] || "");
      } catch (e) {
        if (!cancelled) setError(errorMessage(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void discover();
    return () => {
      cancelled = true;
    };
  }, [api, product.id, retry]);
  async function start() {
    if (uncertain || busy) return;
    setBusy(true);
    setError("");
    try {
      const scope = {
        source,
        ...(demoScope ? { sample_size: 5 as const } : {}),
        ...(source === "amazon_2023" ? { batch_id: batch } : {}),
        ...(date
          ? {
              available_through: new Date(
                date + "T23:59:59.999Z",
              ).toISOString(),
            }
          : {}),
        evaluation: false as const,
      };
      const r = await api.analyze(product.id, { mode, scope });
      onQueued(r);
    } catch (e) {
      if (uncertainWrite(e)) {
        setUncertain(true);
        setError(
          "We could not confirm whether the analysis started. Ask the demo host to check recent runs before starting another, to avoid duplicate work.",
        );
      } else {
        setError(errorMessage(e));
      }
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Find common issues"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <p className="muted">
        Choose the reviews to use for <strong>{product.title}</strong>.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void start();
        }}
      >
        <fieldset disabled={busy || uncertain}>
          <label className="field">
            Analysis size
            <select aria-label="Analysis size" value={demoScope ? "demo" : "full"} onChange={(e) => setDemoScope(e.target.value === "demo")}>
              <option value="demo">Demo: 5 reviews</option>
              <option value="full">Full selected group</option>
            </select>
            <small>{demoScope
              ? "Uses the first five matching reviews in chronological order (or fewer if available). The sample stays fixed for this run."
              : "Large groups may take hours with free-tier pacing."}</small>
          </label>
          <label className="field">
            Reviews to include
            <select
              value={source}
              onChange={(e) => setSource(e.target.value as Source)}
            >
              <option value="amazon_2023">Past Amazon reviews</option>
              <option value="user_submission">New app reviews</option>
            </select>
          </label>
          {source === "amazon_2023" &&
            (loading ? (
              <Loading>Finding review groups…</Loading>
            ) : (
              <label className="field">
                Review group
                <select
                  aria-label="Review group"
                  value={batch}
                  onChange={(e) => setBatch(e.target.value)}
                  required
                >
                  <option value="" disabled>
                    Select a group
                  </option>
                  {batches.map((b) => (
                    <option key={b} value={b}>
                      {batchLabel(b)}
                    </option>
                  ))}
                </select>
                <small>
                  {batches.length
                    ? "One group at a time keeps the analysis focused."
                    : "No available historical groups were found. Try new app reviews."}
                </small>
              </label>
            ))}
          <details className="advanced">
            <summary>Choose an earlier date</summary>
            <label className="field">
              Include reviews up to
              <input
                type="date"
                value={date}
                max={new Date(Date.now() - 86400000).toISOString().slice(0, 10)}
                onChange={(e) => setDate(e.target.value)}
              />
              <small>
                Leave blank to use the group’s latest cutoff. Dates
                use UTC.
              </small>
            </label>
          </details>
          <div className="mode-options">
            <label
              className={"mode-option " + (mode === "memory" ? "selected" : "")}
            >
              <input
                type="radio"
                name="mode"
                checked={mode === "memory"}
                onChange={() => setMode("memory")}
              />
              <span>
                <b>Use previous guidance</b>
                <small>
                  Include relevant context and your team's saved notes.
                </small>
              </span>
            </label>
            <label
              className={
                "mode-option " + (mode === "baseline" ? "selected" : "")
              }
            >
              <input
                type="radio"
                name="mode"
                checked={mode === "baseline"}
                onChange={() => setMode("baseline")}
              />
              <span>
                <b>Start fresh</b>
                <small>Read the reviews without previous guidance.</small>
              </span>
            </label>
          </div>
        </fieldset>
        {error && (
          <ErrorNotice
            message={error}
            retry={
              !busy && !uncertain ? () => setRetry((n) => n + 1) : undefined
            }
          />
        )}
        <p className="fine-print">
          {demoScope ? "Demo sample: up to 5 reviews." : "Up to 1,500 reviews per analysis."} Evaluation group C is excluded.
        </p>
        <button
          className="button primary full"
          disabled={
            busy ||
            uncertain ||
            (source === "amazon_2023" && (loading || !batch))
          }
        >
          {busy ? "Starting…" : "Start analysis"}
        </button>
      </form>
    </Modal>
  );
}
