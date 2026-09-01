"""Liveness check.

Per docs/IMPLEMENTATION-PLAN.md §E: `GET /health` is a liveness check, no auth, no DB
dependency. It is deliberately NOT a DB-readiness probe — the plan specifies liveness only,
and adding a DB check here would be scope beyond what §E defines.
"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")
