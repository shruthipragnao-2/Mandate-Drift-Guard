"""Case-queue read endpoints (Checkpoint C14) plus the HOLD-resolution endpoint (Checkpoint
C12, Plan §E's `POST /cases/{id}/resolve` contract).

Routing/serialization/auth only throughout this file: the read endpoints below are plain
queries over already-written rows (no domain-layer call at all -- there is no state to
compute, only to report), and `resolve_case` wraps `domain.pipeline.resolve_hold` /
`domain.pipeline.check_and_apply_timeout` (Checkpoint C11, already built and verified) -- the
state machine itself is not reimplemented here. Decision 17's single bearer token now also
gates these two read routes, matching the existing extension of that model to
`POST /transactions` -- there is no separate read-only auth tier in this project.

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


class CaseListResponse(BaseModel):
    cases: list[CaseSummary]


@router.get(
    "/cases",
    response_model=CaseListResponse,
    dependencies=[Depends(require_bearer_token)],
)
def list_cases(
    state: Literal["hold", "resolved_allow", "resolved_block"] = "hold",
    db: Session = Depends(get_db),
) -> CaseListResponse:
    rows = (
        db.query(models.Case, models.Mandate.purpose)
        .join(models.Mandate, models.Case.mandate_id == models.Mandate.id)
        .filter(models.Case.state == state)
        .order_by(models.Case.opened_at.desc())
        .all()
    )
    return CaseListResponse(
        cases=[
            CaseSummary(
                id=case.id,
                mandate_id=case.mandate_id,
                transaction_id=case.transaction_id,
                state=case.state,
                opened_at=case.opened_at,
                mandate_purpose=purpose,
            )
            for case, purpose in rows
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
    gate_decision: GateDecisionDetail


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

    # Always present for an opened case -- domain.pipeline._persist_crossed_case writes this
    # row unconditionally once the threshold crosses, before the LLM is ever called, so a
    # case can only exist if its evidence_packet does too.
    evidence_packet = (
        db.query(models.EvidencePacket)
        .filter(models.EvidencePacket.transaction_id == transaction.id)
        .one()
    )
    semantic_assessment = (
        db.query(models.SemanticAssessment)
        .join(models.EvidencePacket)
        .filter(models.EvidencePacket.transaction_id == transaction.id)
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
        ),
        semantic_assessment=SemanticAssessmentDetail(
            risk_level=semantic_assessment.risk_level,
            mandate_alignment=semantic_assessment.mandate_alignment,
            confidence=float(semantic_assessment.confidence),
            evidence=semantic_assessment.evidence,
        )
        if semantic_assessment is not None
        else None,
        gate_decision=GateDecisionDetail(
            decision=gate_decision.decision,
            rule_version=gate_decision.rule_version,
            rule_applied=gate_decision.rule_applied,
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
