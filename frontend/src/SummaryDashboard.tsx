import { useEffect, useRef, useState } from "react";
import type { Api, Product, SummaryVersion, SummaryView } from "./types";
import { errorMessage, uncertainWrite } from "./api";
import { ErrorNotice, Loading } from "./ui";
import { SummaryHistory } from "./SummaryHistory";
import { SummarySettings } from "./SummarySettings";
import { ReviewFeed } from "./ReviewFeed";

function latestView(previous: SummaryView | null, next: SummaryView, product: string): SummaryView | null {
  if (next.product_id !== product) return previous;
  if (previous?.product_id === product && (previous.current?.version ?? 0) > (next.current?.version ?? 0)) return previous;
  return next;
}

function coverageLabel(v: SummaryVersion) {
  return `Based on ${v.coverage.historical_sample_count} sampled historical review${v.coverage.historical_sample_count === 1 ? "" : "s"} + ${v.coverage.new_review_count} new review${v.coverage.new_review_count === 1 ? "" : "s"}`;
}
export function SummaryDashboard({ api, product, reviewVersion = 0, active = true }: { api: Api; product: Product; reviewVersion?: number; active?: boolean }) {
  const [view, setView] = useState<SummaryView | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [historical, setHistorical] = useState<SummaryVersion | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [historyProduct, setHistoryProduct] = useState<string | null>(null);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [refreshError, setRefreshError] = useState("");
  const [reviewRevision, setReviewRevision] = useState<string>();
  const [streamRetry, setStreamRetry] = useState(0);
  const refreshAttempt = useRef<{ product: string; key: string } | null>(null);
  const requestId = useRef(0);
  useEffect(() => {
    const id = ++requestId.current;
    setView(null); setSelectedVersion(null); setHistorical(null); setHistoryProduct(null); setLoading(true); setError(""); setRefreshBusy(false); setRefreshError("");
    setReviewRevision(undefined);
    refreshAttempt.current = null;
    return () => { requestId.current++; };
  }, [product.id]);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const controller = new AbortController();
    if (api.demo) {
      api.summary(product.id).then((next) => {
        if (!cancelled) { setView((previous) => latestView(previous, next, product.id)); setError(""); }
      }).catch((e) => { if (!cancelled) setError(errorMessage(e)); })
        .finally(() => { if (!cancelled) setLoading(false); });
    } else if (api.watchProduct) {
      void api.watchProduct(product.id, (event) => {
        if (cancelled) return;
        if (event.type === "summary") {
          setView((previous) => latestView(previous, event.view, product.id));
          setLoading(false); setError("");
        } else setReviewRevision(event.revision);
      }, controller.signal).catch(() => {
        if (!cancelled) { setError("Live updates are unavailable. Check for updates or retry the connection."); setLoading(false); }
      });
    } else {
      setError("Live updates are unavailable."); setLoading(false);
    }
    return () => { cancelled = true; controller.abort(); };
  }, [api, product.id, active, streamRetry]);
  useEffect(() => {
    if (!api.demo || !reviewVersion) return;
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
    finally { if (id === requestId.current) { setLoading(false); if (!api.demo) setStreamRetry((n) => n + 1); } }
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
    (["pending", "approved", "accepted"].includes(view.initial_candidate.semantic_review?.status ?? ""))
    ? view.initial_candidate : null;
  return <div className="summary-dashboard">
    {!view && loading && <Loading>Loading product summary…</Loading>}
    {error && <ErrorNotice message={error} retry={() => void retryLoad()} />}
    {view && <>
      <div className="summary-statusbar">
        <span className={`pill ${view.status === "failed" ? "failed" : "ready"}`}>
          {candidate ? "Initial summary awaiting publication" : `Summary ${view.status.replaceAll("_", " ")}`}
        </span>
        <span>{view.pending_review_count} pending new review{view.pending_review_count === 1 ? "" : "s"}</span>
        {view.current && <span>Memory sync: {view.memory_status}</span>}
      </div>
      {view.status === "queued" && view.error_code && <p role="status">Summary update retry scheduled automatically. Your reviews are saved; the last published summary remains available.</p>}
      {view.status === "failed" && <ErrorNotice message={`Summary update failed${view.error_code ? ` (${view.error_code})` : ""}. ${view.current ? "The last published version remains available." : "No summary has been published yet."}`} />}
      {!view.current && !candidate && (view.status === "uninitialized"
        ? <section className="empty-state"><h2>No published summary yet.</h2><p>An initial summary is being prepared for this product. This page updates when it is published.</p></section>
        : <section className="empty-state"><h2>No published summary available.</h2><p>Status: {view.status}. This page updates when a summary is published.</p></section>)}
      {candidate && <article className="published-summary candidate-summary">
        <div className="section-heading"><div><span className="eyebrow">AI-generated overview</span><h2>Generated initial summary</h2></div><span className="pill">Awaiting publication</span></div>
        <div className="summary-metrics"><div><span>Review coverage</span><strong>{candidate.coverage.historical_sample_count + candidate.coverage.new_review_count}</strong><small>{coverageLabel(candidate)}</small></div><div><span>Version</span><strong>{candidate.version}</strong><small>Not published</small></div><div><span>Generated</span><strong>{new Date(candidate.created_at).toLocaleDateString()}</strong><small>Initial draft</small></div></div>
        <p className="summary-text">{candidate.narrative}</p>
        {candidate.guidance_references.length > 0 && <p className="fine-print">Guidance used in this draft: {candidate.guidance_references.join(", ")}</p>}
        <p className="fine-print">New reviews will be incorporated after the initial summary is published.</p>
        <ReviewFeed key={product.id} api={api} product={product.id} active={active} revision={reviewRevision} reviewVersion={api.demo ? reviewVersion : 0} />
      </article>}
      {view.current && <>
        {!api.publicDemo && <div className="summary-actions"><button className="button" disabled={refreshBusy || view.pending_review_count === 0} onClick={() => void refresh()}>{refreshBusy ? "Requesting…" : refreshAttempt.current ? "Retry update request" : "Update now"}</button>
          <button className="text-button" onClick={() => void retryLoad()}>Check for updates</button></div>}
        {refreshError && <ErrorNotice message={refreshError} retry={() => void refresh()} />}
        {historyProduct !== product.id && <button className="text-button" onClick={() => setHistoryProduct(product.id)}>View version history</button>}
        {historyProduct === product.id && <SummaryHistory api={api} product={product.id} selectedVersion={selectedVersion} onSelect={setSelectedVersion} />}
        {historyError && <ErrorNotice message={historyError} />}
        {selectedVersion !== null && !historical && !historyError && <Loading>Loading version {selectedVersion}…</Loading>}
        {version && <article className={selectedVersion === null ? "published-summary" : "published-summary historical-summary"}>
          <div className="section-heading"><div><span className="eyebrow">{selectedVersion === null ? "Live product insight" : "Archived product insight"}</span><h2>{selectedVersion === null ? "Current published summary" : `Historical version ${version.version}`}</h2></div><span className="pill ready">Version {version.version}</span></div>
          <div className="summary-metrics"><div><span>Review coverage</span><strong>{version.coverage.historical_sample_count + version.coverage.new_review_count}</strong><small>{coverageLabel(version)}</small></div><div><span>Current version</span><strong>{version.version}</strong><small>{selectedVersion === null ? "Published" : "Historical"}</small></div><div><span>Last updated</span><strong>{new Date(version.published_at || version.created_at).toLocaleDateString()}</strong><small>{new Date(version.published_at || version.created_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</small></div></div>
          <p className="summary-text">{version.narrative}</p>
          {version.guidance_references.length > 0 && <p className="fine-print">Guidance used in this version: {version.guidance_references.join(", ")}</p>}
          {selectedVersion === null && <ReviewFeed key={product.id} api={api} product={product.id} active={active} revision={reviewRevision} reviewVersion={api.demo ? reviewVersion : 0} />}
        </article>}
      </>}
      {!view.current && !candidate && <ReviewFeed key={product.id} api={api} product={product.id} active={active} revision={reviewRevision} reviewVersion={api.demo ? reviewVersion : 0} />}
      {!api.publicDemo && <SummarySettings api={api} product={product.id} value={view.update_threshold} onChanged={setView} />}
    </>}
  </div>;
}
