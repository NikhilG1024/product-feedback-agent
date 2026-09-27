import { useEffect, useState } from "react";
import type { Api, SummaryView } from "./types";
import { errorMessage } from "./api";
import { ErrorNotice } from "./ui";

export function SummarySettings({ api, product, value, onChanged }: {
  api: Api; product: string; value: number; onChanged: (view: SummaryView) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setDraft(String(value)); setError(""); }, [product, value]);
  const threshold = Number(draft);
  const valid = /^\d+$/.test(draft) && Number.isInteger(threshold) && threshold >= 1 && threshold <= 100;
  async function save() {
    if (!valid || busy) return;
    setBusy(true); setError("");
    try { onChanged(await api.summarySettings(product, threshold)); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return <section className="summary-settings" aria-label="Summary settings">
    <h3>Update settings</h3>
    <form onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <label className="field">New reviews before update
        <input aria-label="New reviews before update" inputMode="numeric" type="number" min="1" max="100" step="1" value={draft} onChange={(e) => setDraft(e.target.value)} />
        <small>Choose a whole number from 1 to 100. Default: 1.</small>
      </label>
      {!valid && <p role="alert" className="error">Enter a whole number from 1 to 100.</p>}
      {error && <ErrorNotice message={error} />}
      <button className="button" disabled={!valid || busy || threshold === value}>{busy ? "Saving…" : "Save threshold"}</button>
    </form>
  </section>;
}
