import { useEffect, useState } from "react";
import { ApiError, listCases } from "../api";
import type { CaseSummary } from "../types";

export function CaseQueue({ onOpenCase }: { onOpenCase: (caseId: string) => void }) {
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setCases(null);
    setError(null);
    listCases("hold")
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
          <p className="empty-state-title">No cases on hold.</p>
          <p className="muted">
            Every transaction evaluated so far either matched its mandate or was already
            resolved -- an empty queue is the system working as intended, not an error.
          </p>
        </div>
      )}

      {cases !== null && cases.length > 0 && (
        <table className="case-table">
          <thead>
            <tr>
              <th>Mandate purpose</th>
              <th>Opened</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {cases.map((c) => (
              <tr key={c.id} className="case-row" onClick={() => onOpenCase(c.id)}>
                <td>{c.mandate_purpose}</td>
                <td>{new Date(c.opened_at).toLocaleString()}</td>
                <td className="case-row-action">View &rarr;</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
