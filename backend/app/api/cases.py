"""HOLD-resolution endpoint (Checkpoint C12, Plan §E's `POST /cases/{id}/resolve` contract).

Routing/serialization/auth only: wraps `domain.pipeline.resolve_hold` and
`domain.pipeline.check_and_apply_timeout` (Checkpoint C11, already built and verified) -- the
state machine itself is not reimplemented here. Decision 17: this endpoint requires the same
bearer token as `POST /transactions`.

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
