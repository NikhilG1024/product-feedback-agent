import { useEffect, useRef, useState } from "react";
import type { Api, SummaryVersion } from "./types";
import { errorMessage } from "./api";
import { ErrorNotice } from "./ui";

export function SummaryHistory({ api, product, selectedVersion, onSelect }: {
  api: Api; product: string; selectedVersion: number | null; onSelect: (version: number | null) => void;
}) {
  const [items, setItems] = useState<SummaryVersion[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  async function firstPage() {
    const id = ++generation.current;
    setItems([]); setCursor(null); setLoading(true); setError("");
    try {
      const page = await api.summaryHistory(product);
      if (id !== generation.current) return;
      setItems(page.items); setCursor(page.next_cursor);
    } catch (e) { if (id === generation.current) setError(errorMessage(e)); }
    finally { if (id === generation.current) setLoading(false); }
  }
  useEffect(() => {
    void firstPage();
    return () => { generation.current++; };
  }, [api, product]);
  async function more() {
    if (!cursor || loading) return;
    const id = generation.current;
    setLoading(true); setError("");
    try {
      const page = await api.summaryHistory(product, cursor);
      if (id !== generation.current) return;
      setItems((old) => [...old, ...page.items.filter((v) => !old.some((o) => o.version === v.version))]);
      setCursor(page.next_cursor);
    } catch (e) { if (id === generation.current) setError(errorMessage(e)); }
    finally { if (id === generation.current) setLoading(false); }
  }
  return <section className="summary-history" aria-label="Summary history">
    <h3>Version history</h3>
    {error && <ErrorNotice message={error} retry={() => { if (cursor) void more(); else void firstPage(); }} />}
    <div className="version-list">
      <button className={selectedVersion === null ? "button active" : "button"} onClick={() => onSelect(null)}>Current version</button>
      {items.map((v) => <button key={v.version} className={selectedVersion === v.version ? "button active" : "button"} onClick={() => onSelect(v.version)}>
        Version {v.version} · {v.kind} · {new Date(v.published_at || v.created_at).toLocaleDateString()}
      </button>)}
    </div>
    {loading && <p className="muted">Loading history…</p>}
    {cursor && <button className="text-button" disabled={loading} onClick={() => void more()}>Load older versions</button>}
  </section>;
}
