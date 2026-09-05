"""Case-queue read endpoints (Checkpoint C14, queue redesigned 2026-09-05) plus the
HOLD-resolution endpoint (Checkpoint C12, Plan §E's `POST /cases/{id}/resolve` contract).

Routing/serialization/auth only throughout this file: the read endpoints below are plain
queries over already-written rows (no domain-layer call at all -- there is no state to
compute, only to report), and `resolve_case` wraps `domain.pipeline.resolve_hold` /
`domain.pipeline.check_and_apply_timeout` (Checkpoint C11, already built and verified) -- the
state machine itself is not reimplemented here. Decision 17's single bearer token now also
gates these two read routes, matching the existing extension of that model to
`POST /transactions` -- there is no separate read-only auth tier in this project.

`list_cases`'s `severity` field is the one exception to "no computation" worth calling out
explicitly: it is derived per-request from already-joined columns (never persisted as a new
column, never a new query beyond the joins already needed for merchant/category/amount) --
still routing/serialization in spirit, since it maps existing signal/risk values through a
fixed table rather than deciding anything about the case.

Decision 18's lazy timeout check runs FIRST, before the resolution request is processed: a
case found timed-out on read transitions to `resolved_block` right there, and the resolution
request is then rejected with 409 (the case is no longer `hold` by the time the request would
apply) rather than silently processed against a case that's already effectively closed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_bearer_token
from app.db import models
from app.db.session import get_db
from app.domain.pipeline import InvalidCaseTransitionError, check_and_apply_timeout, resolve_hold

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /cases -- Ops-analyst case queue (Checkpoint C14)
# ---------------------------------------------------------------------------


class CaseSummary(BaseModel):
    id: uuid.UUID
    mandate_id: uuid.UUID
    transaction_id: uuid.UUID
    state: Literal["hold", "resolved_allow", "resolved_block"]
    opened_at: datetime
    mandate_purpose: str
    # Queue redesign (human-approved 2026-09-05): merchant/category/amount were already
    # stored on `transactions` and already one join away -- they just were not serialized by
    # this list endpoint, which previously exposed only mandate_purpose as the row's
    # identifying text. Mandate purpose repeats across many cases against the same recurring
    # mandate; merchant+category+amount is what actually distinguishes one row from the next.
    merchant: str
    category: str
    amount: float
    severity: Literal["high", "medium", "low"]


class CaseListResponse(BaseModel):
    cases: list[CaseSummary]


# Queue redesign: severity is computed per-request from data this endpoint already joins in
# for merchant/category/amount -- not a new query, not a persisted column. Three-way fallback,
# poorest-information-first, human-approved 2026-09-05:
#
#   1. semantic_assessment.risk_level exists -> direct mapping. Decision 6 already guarantees
#      exactly one of low/medium/high, so no translation table is needed for this branch.
#   2. no semantic_assessment but an evidence_packet exists -> the LLM leg failed closed
#      (Decision 14's malformed/timeout/transport_error paths reach the gate as a status, not
#      an exception, and the gate still HOLDs) -- but the deterministic signals are real and
#      known, so the worst of the three bands stands in for the missing LLM read.
#   3. neither exists -> Decision 20's fail-closed exception backstop: the pipeline threw
#      before the evidence packet was ever built. Severity is "high" unconditionally, by
#      explicit instruction -- an unexplained pipeline failure is maximally urgent, not
#      unknown/deprioritized, and must not quietly sort to the bottom of the queue.
_BAND_SEVERITY: dict[str, Literal["high", "medium", "low"]] = {
    "critical": "high",
    "severe": "high",
    "highly_clustered": "high",
    "elevated": "medium",
    "minor": "medium",
    "significant": "medium",
    "clustered": "medium",
    "normal": "low",
    "none": "low",
}

_SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


def _worst_band_severity(signals: dict) -> Literal["high", "medium", "low"]:
    """Branch 2 above. `signals` is `evidence_packets.signals`, always populated with all
    three band keys by `packet_builder.build_evidence_packet` for any case that has an
    evidence_packet row at all -- the `if band in _BAND_SEVERITY` guard and the empty-list
    fallback below are defensive only, not an expected path, and deliberately fail toward
    "high" rather than raising: a queue endpoint that 500s because of a malformed signals blob
    would hide every other case behind it, which is worse than one row reading more urgent
    than it should.
    """
    severities = [
        _BAND_SEVERITY[band]
        for band in (
            signals.get("spend_velocity"),
            signals.get("category_shift"),
            signals.get("clustering"),
        )
        if band in _BAND_SEVERITY
    ]
    if not severities:
        return "high"
    return max(severities, key=lambda sev: _SEVERITY_RANK[sev])


def _compute_severity(
    risk_level: str | None, signals: dict | None
) -> Literal["high", "medium", "low"]:
    if risk_level is not None:
        return risk_level  # type: ignore[return-value]  -- Decision 6 guarantees the value set
    if signals is not None:
        return _worst_band_severity(signals)
    return "high"


@router.get(
    "/cases",
    response_model=CaseListResponse,
    dependencies=[Depends(require_bearer_token)],
)
def list_cases(
    state: Literal["hold", "resolved_allow", "resolved_block"] | None = None,
    db: Session = Depends(get_db),
) -> CaseListResponse:
    """Queue redesign (human-approved 2026-09-05): `state` is now an OPTIONAL narrowing
    filter, not the default view. Omitted, the queue returns cases in all three states --
    the Ops-analyst screen needs the full picture, not just the open backlog, so a resolved
    case does not simply disappear from view. The previous default of `state="hold"` is gone;
    a caller that wants only-hold now passes `?state=hold` explicitly.

    Sort order (human-approved 2026-09-05), computed in Python after fetching rather than
    expressed in SQL: hold cases first, ordered by severity descending then opened_at
    descending within the same severity; both resolved states after, combined and ordered by
    resolved_at descending. A JSONB-aware `CASE WHEN` could express the severity ordering in
    SQL, but at this project's demo scale a Python sort over the full (already-fetched) result
    set is simpler to get right and to verify than hand-rolled SQL band logic, and this
    endpoint has no pagination to make server-side sorting load-bearing.
    """
    query = (
        db.query(
            models.Case,
            models.Mandate.purpose,
            models.Transaction.merchant,
            models.Transaction.category,
            models.Transaction.amount,
            models.EvidencePacket.signals,
            models.SemanticAssessment.risk_level,
        )
        .join(models.Mandate, models.Case.mandate_id == models.Mandate.id)
        .join(models.Transaction, models.Case.transaction_id == models.Transaction.id)
        # Both outer: an evidence_packet may not exist at all (Decision 20 backstop, no
        # threshold-crossing evaluation ever completed), and a semantic_assessment may not
        # exist even when an evidence_packet does (Decision 14's fail-closed LLM statuses).
        .outerjoin(
            models.EvidencePacket,
            models.EvidencePacket.transaction_id == models.Transaction.id,
        )
        .outerjoin(models.GateDecision, models.Case.gate_decision_id == models.GateDecision.id)
        .outerjoin(
            models.SemanticAssessment,
            models.GateDecision.semantic_assessment_id == models.SemanticAssessment.id,
        )
    )
    if state is not None:
        query = query.filter(models.Case.state == state)
    rows = query.all()

    computed = [
        (case, purpose, merchant, category, amount, _compute_severity(risk_level, signals))
        for case, purpose, merchant, category, amount, signals, risk_level in rows
    ]

    def _sort_key(item: tuple) -> tuple[int, int, float]:
        case, _purpose, _merchant, _category, _amount, severity = item
        if case.state == "hold":
            return (0, -_SEVERITY_RANK[severity], -case.opened_at.timestamp())
        # `resolved_at` is set by every path that leaves `hold` (Decision 1's confirm/deny,
        # Decision 18's timeout) -- `or case.opened_at` is a defensive fallback only, so one
        # malformed row degrades to opened_at ordering instead of raising and hiding the
        # entire queue behind a 500.
        resolved_at = case.resolved_at or case.opened_at
        return (1, 0, -resolved_at.timestamp())

    computed.sort(key=_sort_key)

    return CaseListResponse(
        cases=[
            CaseSummary(
                id=case.id,
                mandate_id=case.mandate_id,
                transaction_id=case.transaction_id,
                state=case.state,
                opened_at=case.opened_at,
                mandate_purpose=purpose,
                merchant=merchant,
                category=category,
                amount=float(amount),
                severity=severity,
            )
            for case, purpose, merchant, category, amount, severity in computed
        ]
    )


# ---------------------------------------------------------------------------
# GET /cases/{case_id} -- full pipeline-story detail (Checkpoint C14)
# ---------------------------------------------------------------------------


class MandateDetail(BaseModel):
    purpose: str
    budget: float
    period_days: int
    allowed_categories: list[str]


class TransactionDetail(BaseModel):
    id: uuid.UUID
    merchant: str
    category: str
    amount: float
    occurred_at: datetime
    state: Literal["allowed", "held", "blocked"]


class EvidencePacketDetail(BaseModel):
    signals: dict
    trajectory: dict


class SemanticAssessmentDetail(BaseModel):
    risk_level: str
    mandate_alignment: Literal["low", "medium", "high"]
    confidence: float
    evidence: list[str]
    # Added for the demo-video polish pass (frontend-only in intent -- this is the one field
    # that had to be surfaced here first, since `semantic_assessments.latency_ms` was already
    # stored and already read into this handler's ORM object, just never serialized). Routing/
    # serialization only, per this file's own module docstring -- no domain/pipeline/gate code
    # touched, no new endpoint, no new query, no change to what gets computed or persisted.
    latency_ms: int


class GateDecisionDetail(BaseModel):
    decision: Literal["allow", "hold"]
    rule_version: str
    rule_applied: str


class CaseDetailResponse(BaseModel):
    id: uuid.UUID
    state: Literal["hold", "resolved_allow", "resolved_block"]
    opened_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_reason: str | None
    mandate: MandateDetail
    transaction: TransactionDetail
    evidence_packet: EvidencePacketDetail
    # None specifically when the LLM leg failed (timeout/malformed output, Decision 5) -- the
    # case still opened via the fail-closed path, but no semantic_assessments row was ever
    # written. Distinct from an absent evidence_packet, which never happens for an opened case
    # (domain.pipeline._persist_crossed_case writes it unconditionally once the threshold
    # crosses, before the LLM is ever called).
    semantic_assessment: SemanticAssessmentDetail | None
    # Both of the following became nullable with Decision 20's fail-closed exception backstop
    # (docs/IMPLEMENTATION-BASELINE.md §24). A case opened because the pipeline threw has
    # neither: the exception may have landed before the evidence packet was ever built, and the
    # gate was never reached at all, so by Decision 20 no `gate_decisions` row is written. The
    # comment above about evidence_packet being "always present for an opened case" held
    # exactly until that path existed. `fail_closed_reason` below is what a case detail shows
    # instead, so the UI has something true to render rather than an empty step.
    evidence_packet: EvidencePacketDetail | None
    gate_decision: GateDecisionDetail | None
    # The exception type recorded by the backstop, read back from this case's audit event.
    # None for every ordinary case; set only for a Decision 20 fail-closed hold.
    fail_closed_reason: str | None = None


@router.get(
    "/cases/{case_id}",
    response_model=CaseDetailResponse,
    dependencies=[Depends(require_bearer_token)],
)
def get_case_detail(case_id: uuid.UUID, db: Session = Depends(get_db)) -> CaseDetailResponse:
    case = db.get(models.Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    mandate = case.mandate
    transaction = case.transaction
    gate_decision = case.gate_decision

    # `.first()`, not `.one()`. Until Decision 20 this row was guaranteed for any opened case
    # (`_persist_crossed_case` writes it unconditionally once the threshold crosses, before the
    # LLM is called), and `.one()` asserted that. The fail-closed exception backstop broke the
    # guarantee: a case opened because the pipeline threw may have no evidence packet, and
    # `.one()` would have raised `NoResultFound` -- turning the Ops analyst's attempt to READ a
    # fail-closed case into a second 500, at exactly the moment they most need to see it.
    evidence_packet = (
        db.query(models.EvidencePacket)
        .filter(models.EvidencePacket.transaction_id == transaction.id)
        .first()
    )
    semantic_assessment = (
        db.query(models.SemanticAssessment)
        .join(models.EvidencePacket)
        .filter(models.EvidencePacket.transaction_id == transaction.id)
        .first()
    )
    fail_closed_event = (
        db.query(models.AuditEvent)
        .filter(
            models.AuditEvent.case_id == case.id,
            models.AuditEvent.event_type == "pipeline_exception_fail_closed_hold",
        )
        .first()
    )

    return CaseDetailResponse(
        id=case.id,
        state=case.state,
        opened_at=case.opened_at,
        resolved_at=case.resolved_at,
        resolved_by=case.resolved_by,
        resolution_reason=case.resolution_reason,
        mandate=MandateDetail(
            purpose=mandate.purpose,
            budget=float(mandate.budget),
            period_days=mandate.period_days,
            allowed_categories=mandate.allowed_categories,
        ),
        transaction=TransactionDetail(
            id=transaction.id,
            merchant=transaction.merchant,
            category=transaction.category,
            amount=float(transaction.amount),
            occurred_at=transaction.occurred_at,
            state=transaction.state,
        ),
        evidence_packet=EvidencePacketDetail(
            signals=evidence_packet.signals,
            trajectory=evidence_packet.trajectory,
        )
        if evidence_packet is not None
        else None,
        semantic_assessment=SemanticAssessmentDetail(
            risk_level=semantic_assessment.risk_level,
            mandate_alignment=semantic_assessment.mandate_alignment,
            confidence=float(semantic_assessment.confidence),
            evidence=semantic_assessment.evidence,
            latency_ms=semantic_assessment.latency_ms,
        )
        if semantic_assessment is not None
        else None,
        gate_decision=GateDecisionDetail(
            decision=gate_decision.decision,
            rule_version=gate_decision.rule_version,
            rule_applied=gate_decision.rule_applied,
        )
        if gate_decision is not None
        else None,
        fail_closed_reason=(
            fail_closed_event.payload.get("exception_type") if fail_closed_event else None
        ),
    )


class ResolveRequest(BaseModel):
    resolution: Literal["confirm", "deny"]
    resolved_by: str
    resolution_reason: str


class ResolveResponse(BaseModel):
    case_id: uuid.UUID
    new_state: Literal["resolved_allow", "resolved_block"]
    resolved_at: datetime


@router.post(
    "/cases/{case_id}/resolve",
    response_model=ResolveResponse,
    dependencies=[Depends(require_bearer_token)],
)
def resolve_case(
    case_id: uuid.UUID, request: ResolveRequest, db: Session = Depends(get_db)
) -> ResolveResponse:
    case = db.get(models.Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    # Decision 18: lazy timeout check runs before the resolution request is processed. If this
    # case just timed out, it is now resolved_block -- the branch below rejects the request
    # with 409 rather than proceeding as if it were still open.
    case = check_and_apply_timeout(db, case)

    if case.state != "hold":
        raise HTTPException(
            status_code=409, detail=f"case is not open for resolution (state: {case.state})"
        )

    try:
        case = resolve_hold(
            db,
            case,
            resolution=request.resolution,
            resolved_by=request.resolved_by,
            resolution_reason=request.resolution_reason,
        )
    except InvalidCaseTransitionError as exc:
        # Defensive only -- the state check above already guarantees case.state == "hold"
        # here, so resolve_hold's own guard should never actually fire. Kept so a future race
        # or refactor fails as a 409, not an unhandled 500.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ResolveResponse(case_id=case.id, new_state=case.state, resolved_at=case.resolved_at)
