// Mirrors backend/app/api/cases.py and backend/app/api/transactions.py's Pydantic response
// models field-for-field. Kept as one file, hand-synced -- there is no shared schema codegen
// in this project (FastAPI's own OpenAPI export was out of scope for Checkpoint C14).

export type CaseState = "hold" | "resolved_allow" | "resolved_block";
export type TransactionState = "allowed" | "held" | "blocked";
export type MandateAlignment = "low" | "medium" | "high";
export type GateDecisionValue = "allow" | "hold";

export interface CaseSummary {
  id: string;
  mandate_id: string;
  transaction_id: string;
  state: CaseState;
  opened_at: string;
  mandate_purpose: string;
}

export interface CaseListResponse {
  cases: CaseSummary[];
}

export interface MandateDetail {
  purpose: string;
  budget: number;
  period_days: number;
  allowed_categories: string[];
}

export interface TransactionDetail {
  id: string;
  merchant: string;
  category: string;
  amount: number;
  occurred_at: string;
  state: TransactionState;
}

export interface EvidencePacketDetail {
  signals: {
    spend_velocity?: string;
    category_shift?: string;
    clustering?: string;
    budget_utilization?: number;
    [key: string]: unknown;
  };
  trajectory: {
    historical_distribution?: Record<string, number>;
    current_distribution?: Record<string, number>;
    [key: string]: unknown;
  };
}

export interface SemanticAssessmentDetail {
  risk_level: string;
  mandate_alignment: MandateAlignment;
  confidence: number;
  evidence: string[];
  latency_ms: number;
}

export interface GateDecisionDetail {
  decision: GateDecisionValue;
  rule_version: string;
  rule_applied: string;
}

export interface CaseDetailResponse {
  id: string;
  state: CaseState;
  opened_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  resolution_reason: string | null;
  mandate: MandateDetail;
  transaction: TransactionDetail;
  // evidence_packet and gate_decision became nullable with Decision 20's fail-closed
  // exception backstop: a case opened because the pipeline threw has neither (the throw may
  // have preceded the packet, and the gate was never reached, so no gate_decisions row is
  // written). fail_closed_reason is set only on that path and carries the exception type.
  evidence_packet: EvidencePacketDetail | null;
  semantic_assessment: SemanticAssessmentDetail | null;
  gate_decision: GateDecisionDetail | null;
  fail_closed_reason: string | null;
}

export interface ResolveRequest {
  resolution: "confirm" | "deny";
  resolved_by: string;
  resolution_reason: string;
}

export interface ResolveResponse {
  case_id: string;
  new_state: "resolved_allow" | "resolved_block";
  resolved_at: string;
}

export interface TransactionCreateRequest {
  mandate_id: string;
  merchant: string;
  category: string;
  amount: number;
  occurred_at: string;
  idempotency_key: string;
}

export interface TransactionCreateResponse {
  transaction_id: string;
  state: TransactionState;
  decision: GateDecisionValue;
  case_id: string | null;
  gate_decision: GateDecisionValue | null;
}
