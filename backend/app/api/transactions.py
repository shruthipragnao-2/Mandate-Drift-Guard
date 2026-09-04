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

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import require_bearer_token
from app.config import INGESTION_CONFIG
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

    @field_validator("amount")
    @classmethod
    def _reject_non_finite_amount(cls, value: float) -> float:
        """Red-team findings RT-C1-004 (Infinity) and RT-C1-005 (NaN). JSON's spec has no
        `Infinity`/`NaN`, but Python's `json` module emits and accepts both as an extension,
        so a client can send them and Starlette will parse them. `Field(gt=0)` does not stop
        either: `inf > 0` is True, and NaN slips through pydantic's float handling
        (`allow_inf_nan` defaults on). They then reach persistence and blow up far from the
        cause -- Infinity as
        `psycopg2.errors.InvalidTextRepresentation: invalid input syntax for type json` when
        written to a JSONB column, NaN as
        `ValueError: Out of range float values are not JSON compliant: nan`. Both surfaced as
        HTTP 500 with nothing persisted, where baseline §6 requires an unhandled pipeline
        exception to route to HOLD.

        Worth stating plainly: neither was a fail-OPEN. The evidence engine's band functions
        are `if ratio <= safe_max: return "<safe band>"`, and every comparison against NaN is
        False, so a NaN ratio falls through to the *most severe* band -- the banding is
        fail-closed by construction. These are crashes, not silent ALLOWs.
        """
        if not math.isfinite(value):
            raise ValueError("amount must be a finite number (not NaN or Infinity)")
        return value

    @field_validator("merchant", "category")
    @classmethod
    def _reject_nul_bytes(cls, value: str) -> str:
        """Red-team finding RT-C1-006. A NUL (0x00) inside merchant/category reached
        Postgres and raised `ValueError: A string literal cannot contain NUL (0x00)
        characters` -- HTTP 500, nothing persisted, same §6 violation as above. Postgres TEXT
        genuinely cannot store NUL, so this is refused at the boundary rather than silently
        stripped: quietly rewriting a merchant name would corrupt the audit record that the
        whole system exists to produce.

        Only NUL is rejected. Other unicode -- emoji, RTL marks, combining characters -- is
        left alone deliberately: it stores and renders fine, and merchant names in the real
        world legitimately contain it.
        """
        if "\x00" in value:
            raise ValueError("must not contain NUL (0x00) characters")
        return value

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

    @field_validator("occurred_at")
    @classmethod
    def _reject_future_timestamps(cls, value: datetime) -> datetime:
        """Red-team finding RT-C1-001, the highest-severity one found: `occurred_at` is
        attacker-controlled and feeds compute_velocity's `as_of = max(t.occurred_at ...)`,
        which sets `expected_fraction = days_elapsed / period_days`. Future-dating a
        transaction inflates expected_fraction without bound, so
        `ratio = actual_fraction / expected_fraction` collapses toward zero and the velocity
        band reads "normal" regardless of how large the spend is. For an in-mandate category
        (category_shift "none") on an unclustered day, ALL THREE signals then read benign,
        the threshold is never crossed, and the transaction takes the nominal-ALLOW path:
        no LLM call, no evidence packet, no case, no review. A silent ALLOW -- exactly what
        baseline §6's fail-closed invariant forbids. Confirmed live: an identical 7500-rupee
        spend on an 8000/7-day mandate HELD with an honest timestamp and was ALLOWED when
        dated a year ahead.

        Fixed here, at the ingestion boundary, rather than in the evidence engine, for two
        reasons. First, the engine is a pure function by design and takes no clock; injecting
        wall-clock into it would make signal computation non-deterministic and break the
        reproducibility the eval harness and the locked C13 test-set numbers depend on.
        Second, the engine's unbounded `expected_fraction` growth is the *known* period-
        renewal gap already flagged [OPEN] in baseline §18 and deliberately scoped out by
        Decision 16 -- not something to resolve unilaterally here. What was genuinely
        unguarded, and is genuinely this checkpoint's to close, is that the live API accepted
        arbitrary timestamps at all.
        """
        skew = timedelta(minutes=INGESTION_CONFIG.max_future_skew_minutes)
        if value > datetime.now(timezone.utc) + skew:
            raise ValueError(
                "occurred_at is in the future; a transaction reports spend that has already "
                f"happened (clock-skew allowance: {INGESTION_CONFIG.max_future_skew_minutes} minutes)"
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

    try:
        result = run_pipeline(db, mandate, historical, incoming)
    except IntegrityError:
        # Red-team finding RT-C1-009. The `existing` lookup above and this insert are not one
        # atomic operation, so two concurrent requests carrying the same idempotency key can
        # both read "no existing row" and both proceed. The loser hits
        # `uq_transactions_mandate_id_idempotency_key` and used to surface as HTTP 500 --
        # measured at 3 of 4 concurrent requests. Data integrity was never at risk (exactly
        # one transaction and one case were written either way, so Decision 8's idempotency
        # guarantee held); only the reporting was wrong, which is why this was MODERATE and
        # not a fail-closed violation.
        #
        # Losing the race means the winner has already persisted the identical request, so
        # the correct answer is the replay this endpoint would have returned had the race
        # gone the other way -- the same path a serial duplicate takes above.
        db.rollback()
        raced = (
            db.query(models.Transaction)
            .filter_by(mandate_id=mandate.id, idempotency_key=request.idempotency_key)
            .first()
        )
        if raced is None:
            # Not the idempotency collision -- some other constraint failed, and quietly
            # reporting success for it would be exactly the kind of silent repair this
            # project refuses. Let it propagate.
            raise
        if not _same_payload(raced, request):
            raise HTTPException(
                status_code=409,
                detail="idempotency_key already used for this mandate with a different payload",
            ) from None
        return _response_for_existing(db, raced)

    return TransactionCreateResponse(
        transaction_id=result.transaction_id,
        state=result.state,
        decision=result.gate_decision or "allow",
        case_id=result.case_id,
        gate_decision=result.gate_decision,
    )
