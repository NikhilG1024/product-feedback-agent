import { useState } from "react";
import type { Api, Decision, DecisionInput, Product } from "./types";
import { uncertainWrite, errorMessage } from "./api";
import { ErrorNotice, Modal, StatePill } from "./ui";
export function GuidanceDialog({
  api,
  product,
  evidence,
  onClose,
}: {
  api: Api;
  product: Product;
  evidence: string[];
  onClose: () => void;
}) {
  const [kind, setKind] = useState<DecisionInput["kind"]>("correction"),
    [rationale, setRationale] = useState(""),
    [busy, setBusy] = useState(false),
    [uncertain, setUncertain] = useState(false),
    [startedAt, setStartedAt] = useState(0),
    [error, setError] = useState(""),
    [saved, setSaved] = useState<Decision | null>(null);
  async function submit() {
    if (!rationale.trim() || uncertain || busy) return;
    setStartedAt(Date.now());
    setBusy(true);
    setError("");
    try {
      setSaved(
        await api.decision(product.id, {
          kind,
          rationale,
          evidence_ids: [...new Set(evidence)],
        }),
      );
    } catch (e) {
      if (uncertainWrite(e)) {
        setUncertain(true);
        setError(
          "The server may have saved this note, but we did not receive confirmation. Check before saving again.",
        );
      } else {
        setError(errorMessage(e));
      }
    } finally {
      setBusy(false);
    }
  }
  async function reconcile() {
    setBusy(true);
    try {
      const notes = await api.decisions(product.id);
      const matches = notes.filter(
        (d) =>
          d.kind === kind &&
          d.rationale === rationale &&
          new Date(d.decided_at).getTime() >= startedAt - 5000 &&
          JSON.stringify([...d.evidence_ids].sort()) ===
            JSON.stringify([...new Set(evidence)].sort()),
      );
      if (matches.length === 1) {
        setSaved(matches[0]);
        setError("");
      } else {
        setError(
          "We could not confirm a single saved note. Ask the demo host to check before submitting it again.",
        );
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    if (!saved) return;
    try {
      const list = await api.decisions(product.id);
      const next = list.find((d) => d.id === saved.id);
      if (next) setSaved(next);
      setError("");
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <Modal
      title={saved ? "Your guidance is saved." : "What should we remember?"}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      {saved ? (
        <>
          <p className="muted">
            Saved for {product.title}. Current results stay unchanged.
          </p>
          <blockquote className="guidance-quote">{saved.rationale}</blockquote>
          <div className="status-row">
            <span>Preparing guidance for the AI</span>
            <StatePill
              state={
                saved.processing?.memory_status || saved.processing?.status
              }
            />
          </div>
          <p className="fine-print">
            The next eligible analysis can use this guidance. Earlier historical
            reviews may not be eligible for a note written today.
          </p>
          {error && <ErrorNotice message={error} />}
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
          <p className="muted">
            Help the next analysis understand {product.title}.
          </p>
          <fieldset disabled={busy || uncertain}>
            <label className="field">
              What would you like to add?
              <select
                value={kind}
                onChange={(e) =>
                  setKind(e.target.value as DecisionInput["kind"])
                }
              >
                <option value="correction">Correct a misunderstanding</option>
                <option value="decision">Record a team decision</option>
                <option value="preference">Explain what matters to us</option>
              </select>
            </label>
            <label className="field">
              What should the team know?
              <textarea
                value={rationale}
                maxLength={10000}
                required
                onChange={(e) => setRationale(e.target.value)}
                rows={5}
                placeholder="For example: keep battery wear over time separate from short runtime when new."
              />
            </label>
          </fieldset>
          {!!evidence.length && (
            <p className="fine-print">
              Linked to {new Set(evidence).size} supporting review
              {new Set(evidence).size === 1 ? "" : "s"}.
            </p>
          )}
          {error && <ErrorNotice message={error} />}
          {uncertain && (
            <button
              type="button"
              className="button full"
              disabled={busy}
              onClick={reconcile}
            >
              Check whether it was saved
            </button>
          )}
          <p className="fine-print">
            Saved first, then prepared for future analysis. It will not rewrite
            the current report.
          </p>
          <button
            className="button primary full"
            disabled={busy || uncertain || !rationale.trim()}
          >
            {busy ? "Saving…" : "Save guidance"}
          </button>
        </form>
      )}
    </Modal>
  );
}
