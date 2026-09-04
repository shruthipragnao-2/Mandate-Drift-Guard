import { useEffect, useState } from "react";
import { ApiError, getCaseDetail, resolveCase } from "../api";
import { Badge, toneForAlignment, toneForBand, toneForCategoryShift, toneForRisk } from "../components/Badge";
import type { CaseDetailResponse } from "../types";

export function CaseDetail({
  caseId,
  onResolved,
  onBack,
}: {
  caseId: string;
  onResolved: () => void;
  onBack: () => void;
}) {
  const [detail, setDetail] = useState<CaseDetailResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [resolvedBy, setResolvedBy] = useState("");
  const [resolutionReason, setResolutionReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setLoadError(null);
    getCaseDetail(caseId)
      .then((res) => {
        if (!cancelled) setDetail(res);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(err instanceof ApiError ? `${err.status}: ${JSON.stringify(err.detail)}` : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  async function submitResolution(resolution: "confirm" | "deny") {
    if (!resolvedBy.trim() || !resolutionReason.trim()) {
      setSubmitError("resolved_by and resolution_reason are both required.");
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      await resolveCase(caseId, {
        resolution,
        resolved_by: resolvedBy.trim(),
        resolution_reason: resolutionReason.trim(),
      });
      onResolved();
    } catch (err) {
      setSubmitError(err instanceof ApiError ? `${err.status}: ${JSON.stringify(err.detail)}` : String(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <button className="button-secondary" onClick={onBack}>
          &larr; Back to queue
        </button>
      </div>

      {loadError && <div className="banner banner-error">Failed to load case: {loadError}</div>}
      {!detail && !loadError && <p className="muted">Loading...</p>}

      {detail && (
        <>
          <div className="mandate-context">
            <div>
              <span className="muted">Mandate purpose</span>
              <div className="mandate-context-value">{detail.mandate.purpose}</div>
            </div>
            <div>
              <span className="muted">Budget / period</span>
              <div className="mandate-context-value">
                {detail.mandate.budget} / {detail.mandate.period_days} days
              </div>
            </div>
            <div>
              <span className="muted">Allowed categories</span>
              <div className="mandate-context-value">{detail.mandate.allowed_categories.join(", ")}</div>
            </div>
          </div>

          <ol className="timeline">
            <li className="timeline-step">
              <h2>1. Transaction Received</h2>
              <div className="detail-grid">
                <span className="muted">Merchant</span>
                <span>{detail.transaction.merchant}</span>
                <span className="muted">Category</span>
                <span>{detail.transaction.category}</span>
                <span className="muted">Amount</span>
                <span>{detail.transaction.amount}</span>
                <span className="muted">Occurred at</span>
                <span>{new Date(detail.transaction.occurred_at).toLocaleString()}</span>
              </div>
            </li>

            <li className="timeline-step">
              <h2>2. Deterministic Signals</h2>
              <div className="badge-row">
                {detail.evidence_packet.signals.spend_velocity && (
                  <Badge
                    label={`velocity: ${detail.evidence_packet.signals.spend_velocity}`}
                    tone={toneForBand(detail.evidence_packet.signals.spend_velocity)}
                  />
                )}
                {detail.evidence_packet.signals.category_shift && (
                  <Badge
                    label={`category shift: ${detail.evidence_packet.signals.category_shift}`}
                    tone={toneForCategoryShift(detail.evidence_packet.signals.category_shift)}
                  />
                )}
                {detail.evidence_packet.signals.clustering && (
                  <Badge
                    label={`clustering: ${detail.evidence_packet.signals.clustering}`}
                    tone={toneForBand(detail.evidence_packet.signals.clustering)}
                  />
                )}
              </div>
            </li>

            <li className="timeline-step">
              <h2>
                3. Semantic Assessment <span className="ai-tag">AI</span>
              </h2>
              {detail.semantic_assessment ? (
                <>
                  <div className="badge-row">
                    <Badge
                      label={`risk: ${detail.semantic_assessment.risk_level}`}
                      tone={toneForRisk(detail.semantic_assessment.risk_level)}
                    />
                    <Badge
                      label={`alignment: ${detail.semantic_assessment.mandate_alignment}`}
                      tone={toneForAlignment(detail.semantic_assessment.mandate_alignment)}
                    />
                    <span className="muted">confidence: {detail.semantic_assessment.confidence}</span>
                  </div>
                  <ul className="evidence-list">
                    {detail.semantic_assessment.evidence.map((line, i) => (
                      <li key={i}>{line}</li>
                    ))}
                  </ul>
                </>
              ) : (
                <p className="muted">
                  No assessment available -- the LLM call failed or timed out, and this case was
                  routed to HOLD by the fail-closed path (no signal was silently treated as
                  low-risk).
                </p>
              )}
            </li>

            <li className="timeline-step">
              <h2>4. Gate Decision</h2>
              <div className="badge-row">
                <Badge label={detail.gate_decision.decision} tone={detail.gate_decision.decision === "hold" ? "amber" : "green"} />
                <span className="muted">rule version: {detail.gate_decision.rule_version}</span>
              </div>
              <p className="rule-applied">{detail.gate_decision.rule_applied}</p>
            </li>
          </ol>

          {detail.state === "hold" ? (
            <div className="resolve-panel">
              <h2>Resolve</h2>
              <label>
                Resolved by
                <input
                  type="text"
                  value={resolvedBy}
                  onChange={(e) => setResolvedBy(e.target.value)}
                  placeholder="ops-analyst-1"
                />
              </label>
              <label>
                Resolution reason
                <textarea
                  value={resolutionReason}
                  onChange={(e) => setResolutionReason(e.target.value)}
                  placeholder="Why this case is being confirmed or denied"
                  rows={3}
                />
              </label>
              {submitError && <div className="banner banner-error">{submitError}</div>}
              <div className="resolve-actions">
                <button
                  className="button-confirm"
                  disabled={submitting}
                  onClick={() => submitResolution("confirm")}
                >
                  Confirm (allow)
                </button>
                <button
                  className="button-deny"
                  disabled={submitting}
                  onClick={() => submitResolution("deny")}
                >
                  Deny (block)
                </button>
              </div>
            </div>
          ) : (
            <div className="resolve-panel">
              <h2>Resolution</h2>
              <p>
                Already resolved: <strong>{detail.state}</strong> by {detail.resolved_by} --{" "}
                {detail.resolution_reason}
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
