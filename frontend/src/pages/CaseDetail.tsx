import { useEffect, useState } from "react";
import { ApiError, getCaseDetail, resolveCase } from "../api";
import { Badge, toneForAlignment, toneForBand, toneForCategoryShift, toneForRisk } from "../components/Badge";
import type { CaseDetailResponse } from "../types";

function formatLatency(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

// Demo-polish addition. Reconstructs exactly what packet_builder.build_evidence_packet()
// sends as the LLM's user message (backend/app/domain/evidence_engine/packet_builder.py's
// EvidencePacket model: {mandate, signals, trajectory}) using ONLY fields already present in
// this GET /cases/{id} response -- no new endpoint, no new backend query.
//
// This is not an approximation glued together from loosely-related fields -- it is safe to
// treat as equivalent to the literal packet, for two reasons specific to this backend:
//   1. `mandates` rows are write-once after creation (backend/app/db/models.py's Mandate
//      docstring: purpose/budget/period_days/allowed_categories never change post-issuance),
//      so the live mandate row returned here is identical to whatever MandateSummary the
//      packet was built from at evaluation time -- there is no snapshot to drift from.
//   2. `evidence_packets.signals` / `.trajectory` are stored as exactly
//      `SignalSummary.model_dump()` / `TrajectorySummary.model_dump()` at write time
//      (domain/pipeline.py's _persist_crossed_case) -- reading them back is reading the same
//      dict the packet contained, not a recomputation that could disagree with it.
// Key ORDER below matches EvidencePacket's declared field order for readability; Postgres
// JSONB does not guarantee key order is preserved byte-for-byte on the wire to Anthropic, so
// this is "the same structure and values", not a claim of identical raw bytes.
function reconstructedEvidencePacket(detail: CaseDetailResponse): Record<string, unknown> | null {
  if (!detail.evidence_packet) return null;
  const { signals, trajectory } = detail.evidence_packet;
  return {
    mandate: {
      purpose: detail.mandate.purpose,
      budget: detail.mandate.budget,
      period_days: detail.mandate.period_days,
      allowed_categories: detail.mandate.allowed_categories,
    },
    signals: {
      budget_utilization: signals.budget_utilization,
      spend_velocity: signals.spend_velocity,
      category_shift: signals.category_shift,
      clustering: signals.clustering,
    },
    trajectory: {
      historical_distribution: trajectory.historical_distribution,
      current_distribution: trajectory.current_distribution,
    },
  };
}

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
              {detail.evidence_packet ? (
                <>
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
                  <details className="evidence-packet-disclosure">
                    <summary>Show raw evidence packet</summary>
                    <p className="muted evidence-packet-note">
                      Exactly what layer 2 (the LLM) receives -- reconstructed from this
                      response, not a separate fetch. Note there is no merchant field, and
                      every category below is either an allowed category or the literal
                      bucket &quot;other&quot; (architecture &sect;14 -- structural
                      injection-resistance).
                    </p>
                    <pre className="rule-applied">
                      {JSON.stringify(reconstructedEvidencePacket(detail), null, 2)}
                    </pre>
                  </details>
                </>
              ) : (
                <p className="muted">
                  No signals recorded -- the pipeline raised an unexpected error before this
                  stage completed, and the transaction was held by the fail-closed backstop.
                </p>
              )}
            </li>

            <li className="timeline-step">
              <h2>
                3. Semantic Assessment <span className="ai-tag">AI</span>
                {detail.semantic_assessment && (
                  <span className="muted latency-label">
                    {" "}
                    -- responded in {formatLatency(detail.semantic_assessment.latency_ms)}
                  </span>
                )}
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
              {detail.gate_decision ? (
                <>
                  <div className="badge-row">
                    <Badge label={detail.gate_decision.decision} tone={detail.gate_decision.decision === "hold" ? "amber" : "green"} />
                    <span className="muted">rule version: {detail.gate_decision.rule_version}</span>
                  </div>
                  <p className="rule-applied">{detail.gate_decision.rule_applied}</p>
                </>
              ) : (
                <>
                  <div className="badge-row">
                    <Badge label="hold" tone="amber" />
                    <span className="muted">fail-closed backstop</span>
                  </div>
                  <p className="rule-applied">
                    The gate was never reached: the pipeline raised
                    {detail.fail_closed_reason ? ` ${detail.fail_closed_reason}` : " an unexpected error"}
                    , and this transaction was held rather than allowed. No gate decision is
                    recorded because none was made.
                  </p>
                </>
              )}
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
