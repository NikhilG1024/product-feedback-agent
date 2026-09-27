import type { SummaryVersion } from "./types";

export function SummaryEvidence({ version }: { version: SummaryVersion }) {
  return <section aria-label={`Evidence for version ${version.version}`} className="summary-evidence">
    <h3>What reviewers said</h3>
    {version.themes.length ? version.themes.map((theme) => <article key={theme.id} className="evidence-card">
      <h4>{theme.description}</h4>
      <p className="fine-print">{theme.issue_type.replaceAll("_", " ")} · {theme.polarity}</p>
      {theme.evidence.map((item) => <blockquote key={`${item.review_id}:${item.quote}`}>
        “{item.quote}”<cite className="review-id">Review {item.review_id}</cite>
      </blockquote>)}
    </article>) : <p className="muted">No cited themes in this version.</p>}
    {version.contradictions.length > 0 && <div className="notice"><h4>Different experiences</h4><ul>{version.contradictions.map((c, i) => <li key={i}>{c}</li>)}</ul></div>}
  </section>;
}
