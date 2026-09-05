import { useEffect, useState } from "react";
import { ApiError, listCases } from "../api";
import { Badge, toneForCaseState, toneForRisk } from "../components/Badge";
import type { CaseSummary } from "../types";

export function CaseQueue({ onOpenCase }: { onOpenCase: (caseId: string) => void }) {
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setCases(null);
    setError(null);
    // Queue redesign (2026-09-05): no state argument -- GET /cases now defaults to all three
    // states, and the queue's whole point is to show the full picture, not just the open
    // backlog. `listCases` still accepts an explicit state for narrowing if ever needed.
    listCases()
      .then((res) => {
        if (!cancelled) setCases(res.cases);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? `${err.status}: ${JSON.stringify(err.detail)}` : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  return (
    <div className="page">
      <div className="page-header">
        <h1>Case Queue</h1>
        <button className="button-secondary" onClick={() => setRefreshKey((k) => k + 1)}>
          Refresh
        </button>
      </div>

      {error && <div className="banner banner-error">Failed to load cases: {error}</div>}

      {cases === null && !error && <p className="muted">Loading...</p>}

      {cases !== null && cases.length === 0 && (
        <div className="empty-state">
          <p className="empty-state-title">No cases yet.</p>
          <p className="muted">
            Every transaction evaluated so far has matched its mandate -- no case has ever
            opened, which is the system working as intended, not an error.
          </p>
        </div>
      )}

      {cases !== null && cases.length > 0 && (
        <table className="case-table">
          <thead>
            <tr>
              <th>Transaction</th>
              <th>Severity</th>
              <th>Status</th>
              <th>Opened</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {cases.map((c) => (
              <tr key={c.id} className="case-row" onClick={() => onOpenCase(c.id)}>
                <td>
                  {/* Redesign (2026-09-05): merchant + category + amount are the row's actual
                      distinguishing content -- mandate_purpose repeats across many cases
                      against the same recurring mandate, so it moves to smaller secondary
                      text below rather than being the primary label. */}
                  <div className="case-row-primary">
                    {c.merchant} <span className="muted">&middot; {c.category} &middot; {c.amount}</span>
                  </div>
                  <div className="muted case-row-secondary">{c.mandate_purpose}</div>
                </td>
                <td>
                  <Badge label={c.severity} tone={toneForRisk(c.severity)} />
                </td>
                <td>
                  <Badge label={c.state} tone={toneForCaseState(c.state)} />
                </td>
                <td className="muted case-row-opened">{new Date(c.opened_at).toLocaleString()}</td>
                <td className="case-row-action">View &rarr;</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
