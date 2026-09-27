import { useEffect, useRef, useState } from "react";
import { ArrowRight, Sparkles, MessageSquare } from "lucide-react";
import type { Api, Product, Run } from "./types";
import { analysisFailureMessage, errorMessage } from "./api";
import { AnalysisDialog } from "./AnalysisDialog";
import { EvidenceDialog } from "./EvidenceDialog";
import { GuidanceDialog } from "./GuidanceDialog";
import { Report } from "./Report";
import { Questions } from "./Questions";
import { InitializationProgress } from "./InitializationProgress";
import { batchLabel, dateLabel, ErrorNotice, isFinished, Loading } from "./ui";
export function Dashboard({
  api,
  product,
  reviewVersion,
}: {
  api: Api;
  product: Product;
  reviewVersion: number;
}) {
  const [run, setRun] = useState<Run | null>(null),
    [analysisOpen, setAnalysisOpen] = useState(false),
    [evidence, setEvidence] = useState<string | null>(null),
    [guidance, setGuidance] = useState<string[] | null>(null),
    [error, setError] = useState(""),
    [pollVersion, setPollVersion] = useState(0),
    [history, setHistory] = useState<Run[]>([]),
    [restore, setRestore] = useState(""),
    [restoring, setRestoring] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const currentId = useRef<string | null>(null);
  currentId.current = run?.id || null;
  function accept(next: Run) {
    if (next.parent_asin !== product.id) {
      setError(
        "That analysis belongs to a different product. Select the matching product first.",
      );
      return;
    }
    setRun(next);
    setHistory((list) =>
      [next, ...list.filter((r) => r.id !== next.id)].slice(0, 10),
    );
    setError("");
  }
  useEffect(() => {
    if (!run || isFinished(run.status)) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const id = run.id;
    async function poll() {
      try {
        const next = await api.run(id);
        if (cancelled || currentId.current !== id) return;
        accept(next);
        if (!isFinished(next.status)) timer = setTimeout(poll, 2500);
      } catch (e) {
        if (!cancelled) setError(errorMessage(e));
      }
    }
    timer = setTimeout(poll, api.demo ? 900 : 2500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [run?.id, pollVersion, api]);
  useEffect(() => {
    if (run?.status === "completed" && reviewVersion > 0)
      setRun((r) => (r ? { ...r, stale: true } : r));
  }, [reviewVersion]);
  async function restoreRun() {
    setRestoring(true);
    setError("");
    try {
      const next = await api.run(restore.trim());
      if (alive.current) accept(next);
    } catch (e) {
      if (alive.current) setError(errorMessage(e));
    } finally {
      if (alive.current) setRestoring(false);
    }
  }
  const pending = run && !isFinished(run.status);
  const failure = run?.status === "failed";
  return (
    <>
      <div className="heading">
        <h1>What should we improve?</h1>
        <p>Start with the issues customers mention most.</p>
      </div>
      <InitializationProgress api={api} />
      <div className="analysis-toolbar">
        <div>
          {run ? (
            <>
              <span className="source-label">
                {run.scope.source === "amazon_2023"
                  ? "Past Amazon reviews"
                  : "New app reviews"}
                {run.scope.batch_id
                  ? " · " + batchLabel(run.scope.batch_id)
                  : ""}
              </span>
              <span className="scope-caption">
                {run.scope.sample_size === 5 ? "Demo sample · " : ""}{run.denominator.toLocaleString()} reviews ·{" "}
                {run.mode === "memory"
                  ? "Uses previous guidance"
                  : "Started fresh"}{" "}
                · {dateLabel(run.created_at)}
              </span>
            </>
          ) : (
            <span className="source-label">
              {api.demo
                ? "Sample workspace · No real AI calls"
                : "Choose a review group to get started"}
            </span>
          )}
        </div>
        <button
          className="button primary"
          onClick={() => setAnalysisOpen(true)}
          disabled={!!pending}
        >
          <Sparkles size={16} />
          {pending ? "Analysis in progress" : "Find common issues"}
        </button>
      </div>
      {error && (
        <ErrorNotice
          message={error}
          retry={
            pending
              ? () => {
                  setError("");
                  setPollVersion((n) => n + 1);
                }
              : undefined
          }
        />
      )}{" "}
      {!run ? (
        <section className="empty-state">
          <span className="empty-icon">
            <MessageSquare size={27} />
          </span>
          <h2>Your next insight starts here.</h2>
          <p>
            Find the common issues for <strong>{product.title}</strong>, then
            explore the reviews behind them.
          </p>
          <button className="button" onClick={() => setAnalysisOpen(true)}>
            Choose reviews <ArrowRight size={15} />
          </button>
        </section>
      ) : pending ? (
        <section className="empty-state">
          <Loading>Reading reviews and finding common issues…</Loading>
          <p>
            {run.mode === "memory"
              ? "Relevant context is being prepared before the AI reviews the feedback."
              : "The AI is reviewing this sample without previous guidance."}
          </p>
          <p className="fine-print">
            Results will appear here when processing finishes. You can copy the
            analysis ID below to reopen it later.
          </p>
        </section>
      ) : failure ? (
        <section className="empty-state">
          <h2>This analysis could not finish.</h2>
          <p>{analysisFailureMessage(run.error_code)}</p>
        </section>
      ) : (
        <>
          {run.stale && (
            <div className="notice">
              New feedback or guidance is available. These results still
              describe the original sample. Run a new analysis to update them.
            </div>
          )}
          <Report run={run} demo={api.demo} onEvidence={setEvidence} />
          <Questions key={run.id} api={api} product={product.id} run={run.id} />
          <div className="report-footer">
            <span>Click a chart bar to read the supporting reviews.</span>
            <button className="text-button" onClick={() => setGuidance([])}>
              Add guidance for the next summary <ArrowRight size={13} />
            </button>
          </div>
        </>
      )}
      <details className="history">
        <summary>Analysis history</summary>
        {run && (
          <p className="fine-print">
            Current analysis ID: <code className="run-id">{run.id}</code>
          </p>
        )}
        {history.length > 1 && (
          <label className="field">
            Reports opened in this session
            <select
              value={run?.id || ""}
              onChange={(e) => {
                const r = history.find((r) => r.id === e.target.value);
                if (r) accept(r);
              }}
            >
              {history.map((r) => (
                <option key={r.id} value={r.id}>
                  {dateLabel(r.created_at)} ·{" "}
                  {r.mode === "memory" ? "With guidance" : "Fresh"} ·{" "}
                  {r.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
        )}
        <form
          className="restore-form"
          onSubmit={(e) => {
            e.preventDefault();
            void restoreRun();
          }}
        >
          <label className="field">
            Open an analysis by ID
            <input
              value={restore}
              onChange={(e) => setRestore(e.target.value)}
              required
              maxLength={200}
              placeholder="Paste an analysis ID"
            />
          </label>
          <button className="button" disabled={restoring || !restore.trim()}>
            {restoring ? "Opening…" : "Open"}
          </button>
        </form>
        <p className="fine-print">
          Reports must belong to this product. Browser refresh clears the
          session list. Separate runs may use different review samples.
        </p>
      </details>
      {analysisOpen && (
        <AnalysisDialog
          api={api}
          product={product}
          onClose={() => setAnalysisOpen(false)}
          onQueued={(r) => {
            if (alive.current) {
              accept(r);
              setAnalysisOpen(false);
            }
          }}
        />
      )}
      {evidence && run && (
        <EvidenceDialog
          key={run.id + evidence}
          api={api}
          product={product}
          run={run}
          findingId={evidence}
          onClose={() => setEvidence(null)}
          onGuidance={(ids) => {
            setEvidence(null);
            setGuidance(ids);
          }}
        />
      )}
      {guidance && (
        <GuidanceDialog
          api={api}
          product={product}
          evidence={guidance}
          onClose={() => setGuidance(null)}
        />
      )}
    </>
  );
}
