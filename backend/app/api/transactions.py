"""Ingestion endpoint (Checkpoint C12, Plan §E's `POST /mandates/{id}/transactions` contract
-- mounted here at `POST /transactions` with `mandate_id` in the request body rather than the
path, since no `api/mandates.py` router exists yet to nest under; the contract's fields and
behavior are otherwise unchanged).

Routing/serialization/auth only: this module wraps `domain.pipeline.run_pipeline` (Checkpoint
C11, already built and verified) and `domain.pipeline.IncomingTransaction` -- it does not
implement pipeline logic itself. Decision 17: this endpoint requires the same bearer token as
`POST /cases/{id}/resolve`.

Idempotency (Decision 8, already locked -- scoped per `(mandate_id, idempotency_key)`, matching
the DB's own unique constraint): a repeat request with the same key and an identical payload
returns the already-computed prior result without re-running the pipeline; the same key with a
*different* payload is a 409 conflict, per Plan §E's own proposed convention.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.auth import require_bearer_token
from app.db import models
from app.db.session import get_db
from app.domain.pipeline import IncomingTransaction, run_pipeline

router = APIRouter()


class TransactionCreateRequest(BaseModel):
    mandate_id: uuid.UUID
    merchant: str
    category: str
    amount: float = Field(gt=0)
    occurred_at: datetime
    idempotency_key: str

    @field_validator("occurred_at")
    @classmethod
    def _require_explicit_timezone(cls, value: datetime) -> datetime:
        """Red-team finding RT-C1-003: a naive `occurred_at` (e.g. "2026-09-02T10:00:00", a
        perfectly ordinary ISO spelling) reached compute_velocity and raised
        `TypeError: can't subtract offset-naive and offset-aware datetimes` against the
        mandate's tz-aware `created_at` -- a 500, where baseline §6 requires an unhandled
        pipeline exception to route to HOLD.

        Rejected rather than silently assumed to be UTC: every timestamp this system stores,
        compares, and reasons about is tz-aware, and guessing an offset would shift a
        transaction by hours and change its clustering band. That silent-repair-of-ambiguous-
        input is exactly what this project refuses elsewhere (Decision 3: "not repaired, not
        defaulted, not best-effort parsed"). Surfacing it as a 400 with an actionable message
        is both safe and honest.
        """
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError(
                "occurred_at must include an explicit timezone offset "
                '(e.g. "2026-09-02T10:00:00Z" or "2026-09-02T10:00:00+05:30")'
            )
        return value


class TransactionCreateResponse(BaseModel):
    transaction_id: uuid.UUID
    # Broadened beyond Plan §E's original illustrative "allowed"|"held" pair to include
    # "blocked": an idempotent replay reflects the transaction's CURRENT state, which may have
    # moved to "blocked" via HOLD resolution/timeout since the original ingestion -- Plan §E
    # predates the full resolution state machine (C11), and a stale reply would be dishonest.
    state: Literal["allowed", "held", "blocked"]
    decision: Literal["allow", "hold"]
    case_id: uuid.UUID | None = None
    # None specifically when the deterministic threshold was never crossed -- the gate was
    # never invoked at all for this transaction, which is a distinct, honestly-reported fact
    # from "the gate said allow".
    gate_decision: Literal["allow", "hold"] | None = None


def _same_payload(existing: models.Transaction, request: TransactionCreateRequest) -> bool:
    return (
        existing.merchant == request.merchant
        and existing.category == request.category
        and float(existing.amount) == float(request.amount)
        and existing.occurred_at == request.occurred_at
    )


def _response_for_existing(db: Session, txn: models.Transaction) -> TransactionCreateResponse:
    case = db.query(models.Case).filter_by(transaction_id=txn.id).first()
    gate_decision_row = db.query(models.GateDecision).filter_by(transaction_id=txn.id).first()
    decision = gate_decision_row.decision if gate_decision_row else "allow"
    return TransactionCreateResponse(
        transaction_id=txn.id,
        state=txn.state,
        decision=decision,
        case_id=case.id if case else None,
        gate_decision=gate_decision_row.decision if gate_decision_row else None,
    )


@router.post(
    "/transactions",
    response_model=TransactionCreateResponse,
    dependencies=[Depends(require_bearer_token)],
)
def create_transaction(
    request: TransactionCreateRequest, db: Session = Depends(get_db)
) -> TransactionCreateResponse:
    mandate = db.get(models.Mandate, request.mandate_id)
    if mandate is None:
        raise HTTPException(status_code=404, detail="mandate not found")

    existing = (
        db.query(models.Transaction)
        .filter_by(mandate_id=mandate.id, idempotency_key=request.idempotency_key)
        .first()
    )
    if existing is not None:
        if not _same_payload(existing, request):
            raise HTTPException(
                status_code=409,
                detail="idempotency_key already used for this mandate with a different payload",
            )
        return _response_for_existing(db, existing)

    historical = (
        db.query(models.Transaction)
        .filter(models.Transaction.mandate_id == mandate.id)
        .order_by(models.Transaction.occurred_at)
        .all()
    )
    incoming = IncomingTransaction(
        merchant=request.merchant,
        category=request.category,
        amount=request.amount,
        occurred_at=request.occurred_at,
        idempotency_key=request.idempotency_key,
    )

    result = run_pipeline(db, mandate, historical, incoming)

    return TransactionCreateResponse(
        transaction_id=result.transaction_id,
        state=result.state,
        decision=result.gate_decision or "allow",
        case_id=result.case_id,
        gate_decision=result.gate_decision,
    )
