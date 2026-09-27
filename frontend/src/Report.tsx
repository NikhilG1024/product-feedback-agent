import type { Run } from "./types";
import { ChevronDown, ArrowUpRight } from "lucide-react";
export function Report({
  run,
  demo,
  onEvidence,
}: {
  run: Run;
  demo: boolean;
  onEvidence: (id: string) => void;
}) {
  if (run.status !== "completed") return null;
  const counts = run.supporting_review_counts || [];
  const max = Math.max(1, ...counts.map((c) => c.supporting_review_count));
  const axis = Math.ceil(max / 10) * 10;
  return (
    <>
      <section className="summary-card">
        <div className="summary-copy">
          <div className="section-heading">
            <h2>What customers are saying</h2>
            {demo && <span className="pill">Sample summary</span>}
          </div>
          <p className="summary-text">
            {run.summary || "No summary was returned for this analysis."}
          </p>
          {run.mode === "memory" ? (
            <details className="guidance">
              <summary>
                Previous guidance used <ChevronDown size={13} />
              </summary>
              {run.guidance_references?.length ? (
                <ul>
                  {run.guidance_references.map((g) => (
                    <li key={g.decision_id}>{g.rationale}</li>
                  ))}
                </ul>
              ) : (
                <p>No saved team guidance was referenced in this analysis.</p>
              )}
            </details>
          ) : (
            <span className="pill">Started fresh · No previous guidance</span>
          )}
        </div>
        <figure className="issue-chart">
          <figcaption>
            How often each issue appears
            <span>
              Supporting reviews · out of {run.denominator.toLocaleString()}
            </span>
          </figcaption>
          {counts.length ? (
            <>
              <div className="chart-rows">
                {counts.map((c) => (
                  <button
                    key={c.finding_id}
                    className="chart-row"
                    onClick={() => onEvidence(c.finding_id)}
                    aria-label={`${c.theme}: ${c.supporting_review_count} of ${run.denominator} reviews. Read supporting reviews.`}
                  >
                    <span>{c.theme}</span>
                    <span className="chart-track">
                      <i
                        style={{
                          width: `${Math.min(100, (c.supporting_review_count / axis) * 100)}%`,
                        }}
                      />
                    </span>
                    <b>{c.supporting_review_count}</b>
                    <ArrowUpRight size={12} aria-hidden="true" />
                  </button>
                ))}
              </div>
              <div className="chart-axis" aria-hidden="true">
                <span>0</span>
                <span>{axis / 2}</span>
                <span>{axis} reviews</span>
              </div>
            </>
          ) : (
            <p className="muted">
              No recurring issues were returned for this sample.
            </p>
          )}
          <p className="fine-print">
            A review can mention more than one issue.
          </p>
        </figure>
      </section>
      <details className="report-notes">
        <summary>About these results</summary>
        <ul>
          {(run.limitations || []).map((text, i) => (
            <li key={i}>{text}</li>
          ))}
        </ul>
        {!!run.investigation_suggestions?.length && (
          <>
            <h3>What to check next</h3>
            <ul>
              {run.investigation_suggestions.map((t, i) => (
                <li key={i}>{t}</li>
              ))}
            </ul>
          </>
        )}
        <p>
          Review counts come from the selected sample. Customer reports are not
          proof of a technical cause.
        </p>
      </details>
    </>
  );
}
