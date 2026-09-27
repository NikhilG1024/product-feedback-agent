import { useRef, useEffect, useState } from "react";
import type { Api, Answer } from "./types";
import { errorMessage } from "./api";
import { ErrorNotice } from "./ui";
export function Questions({
  api,
  product,
  run,
}: {
  api: Api;
  product: string;
  run: string;
}) {
  const [question, setQuestion] = useState(""),
    [answer, setAnswer] = useState<Answer | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const alive = useRef(true);
  useEffect(
    () => () => {
      alive.current = false;
    },
    [],
  );
  async function ask() {
    if (!question.trim() || busy) return;
    setBusy(true);
    setError("");
    setAnswer(null);
    try {
      const result = await api.question(product, run, question);
      if (alive.current) setAnswer(result);
    } catch (e) {
      if (alive.current) setError(errorMessage(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <details className="questions">
      <summary>Ask about these reviews</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask();
        }}
      >
        <label className="field">
          Your question
          <input
            value={question}
            maxLength={2000}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="What supports the main concern?"
            required
            disabled={busy}
          />
        </label>
        <button className="button" disabled={busy || !question.trim()}>
          {busy ? "Checking reviews…" : "Ask"}
        </button>
      </form>
      {error && <ErrorNotice message={error} />}{" "}
      {answer && (
        <div className="answer" role="status">
          {answer.insufficient_evidence && (
            <span className="pill">Not enough evidence</span>
          )}
          <p>{answer.answer}</p>
          {answer.evidence.map((e, i) => (
            <blockquote key={e.review_id + ":" + i}>
              “{e.quote}”<cite>Review {e.review_id}</cite>
            </blockquote>
          ))}
        </div>
      )}
    </details>
  );
}
