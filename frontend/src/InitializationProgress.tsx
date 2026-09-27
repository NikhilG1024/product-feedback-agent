import { useEffect, useRef, useState } from "react";
import type { Api, InitializationProgressResponse } from "./types";

const label = {
  queued: "Queued",
  generating: "Generating",
  citation_checks_passed: "Citation checks passed",
  reused: "Reused and citation-checked",
  needs_review: "Needs review",
};

export function InitializationProgress({ api }: { api: Api }) {
  const [result, setResult] = useState<InitializationProgressResponse | null>(null);
  const [items, setItems] = useState<NonNullable<InitializationProgressResponse["progress"]>["products"]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadError, setLoadError] = useState(false);
  const currentRunId = useRef<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api.summaryProgress();
        if (cancelled) return;
        currentRunId.current = next.progress?.run_id ?? null;
        setResult(next);
        setItems(next.progress?.products ?? []);
        setNextOffset(next.progress?.next_offset ?? null);
        setLoadError(false);
        if (!api.demo && (!next.progress || next.progress.status === "running")) timer = setTimeout(poll, 5000);
      } catch {
        if (cancelled) return;
        setLoadError(true);
        timer = setTimeout(poll, 5000);
      }
    }
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [api]);
  async function more() {
    if (nextOffset === null || !result?.progress) return;
    const runId = result.progress.run_id;
    try {
      const next = await api.summaryProgress(nextOffset);
      if (currentRunId.current !== runId || next.progress?.run_id !== runId) return;
      setItems((current) => [...current, ...next.progress!.products]);
      setNextOffset(next.progress?.next_offset ?? null);
    } catch { setLoadError(true); }
  }
  const progress = result?.progress;
  const processed = progress ? progress.completed + progress.failed : 0;
  const now = Date.now();
  const elapsed = progress ? Math.max(0, (now - Date.parse(progress.started_at)) / 1000) : 0;
  const remainingSeconds = progress && result?.availability === "available" && progress.status === "running" && processed >= 6
    ? Math.max(0, Math.ceil(elapsed / processed * (progress.total - processed)))
    : null;
  const remainingMinutes = remainingSeconds === null ? null : Math.max(1, Math.round(remainingSeconds / 60));
  return (
    <section className="notice" aria-label="Initialization progress">
      <h2>Product summary initialization</h2>
      {api.demo ? (
        <p>Live initialization progress is unavailable in sample mode.</p>
      ) : loadError ? (
        <p>Progress could not be loaded. Checking again shortly.</p>
      ) : !result ? (
        <p>Loading initialization progress…</p>
      ) : result.availability === "unavailable" || !progress ? (
        <p>Initialization progress is unavailable.</p>
      ) : (
        <>
          {result.availability === "stale" && <p role="status">Progress has not updated recently. A product may still be generating.</p>}
          <p>{progress.completed} citation-checked · {progress.active} active · {progress.queued} queued · {progress.failed} need review · {progress.total} total</p>
          <p>Published summaries: unknown. Citation checks do not publish a summary.</p>
          <p>Last progress update: {new Date(progress.updated_at).toLocaleString()} · Run {progress.run_id}</p>
          {progress.status === "running" && <p>{result.availability === "stale" ? "Elapsed since start (progress stale)" : "Elapsed"}: {Math.floor(elapsed / 60)} min</p>}
          {progress.status !== "running" ? (
            <p>Initialization finished. Semantic review and publication are separate steps.</p>
          ) : result.availability === "stale" ? (
            <p>Estimate unavailable until progress updates.</p>
          ) : remainingSeconds === null ? (
            <p>Estimating finish after 6 products have been processed.</p>
          ) : (
            <>
              <p>Approx. {remainingMinutes} min remaining to finish initialization. Excludes semantic review and publication.</p>
              <p>Estimated local finish: {new Date(now + remainingSeconds * 1000).toLocaleString()}</p>
            </>
          )}
          <ul>
            {items.map((item) => <li key={item.id}>{item.title} · {label[item.status]}</li>)}
          </ul>
          {nextOffset !== null && <button className="button" onClick={() => void more()}>Show more products</button>}
        </>
      )}
    </section>
  );
}
