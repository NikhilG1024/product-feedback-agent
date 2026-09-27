import { useEffect, useRef, useState } from "react";
import type { Answer, Api, Product, SummaryVersion, SummaryView } from "./types";
import { errorMessage, uncertainWrite } from "./api";
import { ErrorNotice, Loading } from "./ui";
import { SummaryHistory } from "./SummaryHistory";
import { SummarySettings } from "./SummarySettings";
import { ReviewFeed } from "./ReviewFeed";

const SUMMARY_POLL_MS = 5000;

function latestView(previous: SummaryView | null, next: SummaryView, product: string): SummaryView | null {
  if (next.product_id !== product) return previous;
  if (previous?.product_id === product && (previous.current?.version ?? 0) > (next.current?.version ?? 0)) return previous;
  return next;
}

function coverageLabel(v: SummaryVersion) {
  return `Based on ${v.coverage.historical_sample_count} sampled historical review${v.coverage.historical_sample_count === 1 ? "" : "s"} + ${v.coverage.new_review_count} new review${v.coverage.new_review_count === 1 ? "" : "s"}`;
}
function SummaryQuestions({ api, product, version }: { api: Api; product: string; version: number }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const requestId = useRef(0);
  useEffect(() => { requestId.current++; setAnswer(null); setError(""); setQuestion(""); }, [product, version]);
  async function ask() {
    if (!question.trim() || busy) return;
    const id = ++requestId.current;
    setBusy(true); setError(""); setAnswer(null);
    try {
      const result = await api.summaryQuestion(product, version, question.trim());
      if (id === requestId.current) setAnswer(result);
    } catch (e) { if (id === requestId.current) setError(errorMessage(e)); }
    finally { if (id === requestId.current) setBusy(false); }
  }
  return <section className="summary-questions" aria-label={`Questions about version ${version}`}>
    <h3>Ask about version {version}</h3>
    <form onSubmit={(e) => { e.preventDefault(); void ask(); }}>
      <label className="field">Question
        <input value={question} maxLength={1000} onChange={(e) => setQuestion(e.target.value)} placeholder="What do reviewers say about…?" />
      </label>
      <button className="button primary" disabled={busy || !question.trim()}>{busy ? "Asking…" : "Ask"}</button>
    </form>
    {error && <ErrorNotice message={error} />}
    {answer && <div className="answer"><p>{answer.answer}</p>
      {answer.insufficient_evidence && <p>There is not enough evidence in this version to answer confidently.</p>}
      {answer.evidence.map((e) => <blockquote key={`${e.review_id}:${e.quote}`}>{e.quote}<cite>Review {e.review_id}</cite></blockquote>)}
    </div>}
  </section>;
}

export function SummaryDashboard({ api, product, reviewVersion = 0 }: { api: Api; product: Product; reviewVersion?: number }) {
  const [view, setView] = useState<SummaryView | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [historical, setHistorical] = useState<SummaryVersion | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [historyProduct, setHistoryProduct] = useState<string | null>(null);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [refreshError, setRefreshError] = useState("");
  const refreshAttempt = useRef<{ product: string; key: string } | null>(null);
  const requestId = useRef(0);
  useEffect(() => {
    const id = ++requestId.current;
    setView(null); setSelectedVersion(null); setHistorical(null); setHistoryProduct(null); setLoading(true); setError(""); setRefreshBusy(false); setRefreshError("");
    refreshAttempt.current = null;
    api.summary(product.id).then((next) => { if (id === requestId.current) setView((previous) => latestView(previous, next, product.id)); })
      .catch((e) => { if (id === requestId.current) setError(errorMessage(e)); })
      .finally(() => { if (id === requestId.current) setLoading(false); });
    return () => { requestId.current++; };
  }, [api, product.id]);
  useEffect(() => {
    if (api.demo) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api.summary(product.id);
        if (!cancelled) {
          setView((previous) => latestView(previous, next, product.id));
          if (next.product_id === product.id) setError("");
        }
      } catch (e) {
        if (!cancelled) setError(errorMessage(e));
      } finally {
        if (!cancelled) timer = setTimeout(poll, SUMMARY_POLL_MS);
      }
    }
    timer = setTimeout(poll, SUMMARY_POLL_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [api, product.id]);
  useEffect(() => {
    if (!reviewVersion) return;
    const id = requestId.current;
    api.summary(product.id).then((next) => { if (id === requestId.current) setView((previous) => latestView(previous, next, product.id)); })
      .catch((e) => { if (id === requestId.current) setError(errorMessage(e)); });
  }, [reviewVersion, api, product.id]);
  useEffect(() => {
    if (selectedVersion === null) { setHistorical(null); setHistoryError(""); return; }
    let cancelled = false;
    setHistorical(null); setHistoryError("");
    api.summaryVersion(product.id, selectedVersion).then((v) => {
      if (!cancelled && v.product_id === product.id && v.version === selectedVersion) setHistorical(v);
    }).catch((e) => { if (!cancelled) setHistoryError(errorMessage(e)); });
    return () => { cancelled = true; };
  }, [api, product.id, selectedVersion]);
  async function retryLoad() {
    const id = ++requestId.current;
    setLoading(true); setError("");
    try { const next = await api.summary(product.id); if (id === requestId.current) setView((previous) => latestView(previous, next, product.id)); }
    catch (e) { if (id === requestId.current) setError(errorMessage(e)); }
    finally { if (id === requestId.current) setLoading(false); }
  }
  async function refresh() {
    if (refreshBusy) return;
    const id = requestId.current;
    if (!refreshAttempt.current || refreshAttempt.current.product !== product.id) refreshAttempt.current = { product: product.id, key: crypto.randomUUID() };
    setRefreshBusy(true); setRefreshError("");
    try {
      const next = await api.refreshSummary(product.id, "pending_reviews", refreshAttempt.current.key);
      if (id === requestId.current && next.product_id === product.id) { setView((previous) => latestView(previous, next, product.id)); refreshAttempt.current = null; }
    } catch (e) {
      if (id === requestId.current) {
        setRefreshError(errorMessage(e));
        if (!uncertainWrite(e)) refreshAttempt.current = null;
      }
    } finally { if (id === requestId.current) setRefreshBusy(false); }
  }
  const version = selectedVersion === null ? view?.current : historical;
  const candidate = !view?.current && view?.initial_candidate?.product_id === product.id &&
    view.initial_candidate.kind === "initial" && view.initial_candidate.published_at === null &&
    (view.initial_candidate.semantic_review?.status === "pending" || view.initial_candidate.semantic_review?.status === "approved")
    ? view.initial_candidate : null;
  return <div className="summary-dashboard">
    <div className="heading"><h1>{product.title}</h1><p>Customer feedback and the evidence behind it.</p></div>
    {!view && loading && <Loading>Loading product summary…</Loading>}
    {error && <ErrorNotice message={error} retry={() => void retryLoad()} />}
    {view && <>
      <div className="summary-statusbar">
        <span className={`pill ${view.status === "failed" ? "failed" : "ready"}`}>
          {candidate ? `Initial summary ${candidate.semantic_review?.status === "approved" ? "awaiting publication" : "validation pending"}` : `Summary ${view.status.replaceAll("_", " ")}`}
        </span>
        <span>{view.pending_review_count} pending new review{view.pending_review_count === 1 ? "" : "s"}</span>
        {view.current && <span>Memory sync: {view.memory_status}</span>}
      </div>
      {view.status === "failed" && <ErrorNotice message={`Summary update failed${view.error_code ? ` (${view.error_code})` : ""}. ${view.current ? "The last published version remains available." : "No summary has been published yet."}`} />}
      {!view.current && !candidate && (view.status === "uninitialized"
        ? <section className="empty-state"><h2>No published summary yet.</h2><p>A reviewed summary is being prepared for this product. This page updates when it is published.</p></section>
        : <section className="empty-state"><h2>No published summary available.</h2><p>Status: {view.status}. This page updates when a summary is published.</p></section>)}
      {candidate && <article className="published-summary candidate-summary">
        <div className="section-heading"><h2>Generated initial summary</h2><span className="pill">{candidate.semantic_review?.status === "approved" ? "Awaiting publication" : "Validation pending"}</span></div>
        <p className="summary-meta">Generated {new Date(candidate.created_at).toLocaleString()} · Version {candidate.version} · Not published</p>
        <p className="source-label">{coverageLabel(candidate)}</p>
        <p className="summary-text">{candidate.narrative}</p>
        {candidate.guidance_references.length > 0 && <p className="fine-print">Guidance used in this draft: {candidate.guidance_references.join(", ")}</p>}
        <p className="fine-print">New reviews will be incorporated after the initial summary is published.</p>
        <ReviewFeed key={product.id} api={api} product={product.id} reviewVersion={reviewVersion} />
      </article>}
      {view.current && <>
        <div className="summary-actions"><button className="button" disabled={refreshBusy || view.pending_review_count === 0} onClick={() => void refresh()}>{refreshBusy ? "Requesting…" : refreshAttempt.current ? "Retry update request" : "Update now"}</button>
          <button className="text-button" onClick={() => void retryLoad()}>Check for updates</button></div>
        {refreshError && <ErrorNotice message={refreshError} retry={() => void refresh()} />}
        {historyProduct !== product.id && <button className="text-button" onClick={() => setHistoryProduct(product.id)}>View version history</button>}
        {historyProduct === product.id && <SummaryHistory api={api} product={product.id} selectedVersion={selectedVersion} onSelect={setSelectedVersion} />}
        {historyError && <ErrorNotice message={historyError} />}
        {selectedVersion !== null && !historical && !historyError && <Loading>Loading version {selectedVersion}…</Loading>}
        {version && <article className={selectedVersion === null ? "published-summary" : "published-summary historical-summary"}>
          <div className="section-heading"><h2>{selectedVersion === null ? "Current published summary" : `Historical version ${version.version}`}</h2><span className="pill ready">Version {version.version}</span></div>
          <p className="summary-meta">Last updated {new Date(version.published_at || version.created_at).toLocaleString()}</p>
          <p className="source-label">{coverageLabel(version)}</p>
          <p className="summary-text">{version.narrative}</p>
          {version.guidance_references.length > 0 && <p className="fine-print">Guidance used in this version: {version.guidance_references.join(", ")}</p>}
          {selectedVersion === null && <ReviewFeed key={product.id} api={api} product={product.id} reviewVersion={reviewVersion} />}
          <SummaryQuestions key={`${product.id}:${version.version}`} api={api} product={product.id} version={version.version} />
        </article>}
      </>}
      {!view.current && !candidate && <ReviewFeed key={product.id} api={api} product={product.id} reviewVersion={reviewVersion} />}
      <SummarySettings api={api} product={product.id} value={view.update_threshold} onChanged={setView} />
    </>}
  </div>;
}
