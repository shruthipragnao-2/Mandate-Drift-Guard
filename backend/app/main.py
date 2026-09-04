"""FastAPI application entrypoint.

Checkpoint C5 / milestone M0 built app startup, config loading, logging, and the health
endpoint. Checkpoint C12 adds the ingestion and HOLD-resolution routers (routing/serialization/
auth only -- both wrap the already-built, unmodified `domain.pipeline` orchestrator).
Checkpoint C14 adds CORS: the frontend (Vite dev server, a different origin even on
localhost -- port alone makes it cross-origin) would otherwise have every request blocked by
the browser before auth is ever checked, per M7's exit bar of an Ops analyst reaching the
backend from a real browser. `allow_origins` is limited to the two Vite dev-server origin
spellings, not a wildcard -- Decision 17's bearer token still gates every route; this only
lets the browser make the request at all.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.cases import router as cases_router
from app.api.health import router as health_router
from app.api.transactions import router as transactions_router
from app.config import settings

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mandate_drift_guard")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup app_env=%s", settings.app_env)
    yield
    logger.info("shutdown")


app = FastAPI(title="Mandate Drift Guard", version="0.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health_router)
app.include_router(transactions_router)
app.include_router(cases_router)


@app.exception_handler(RequestValidationError)
async def _validation_error_as_400(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's default for a request-body/path validation failure is 422 Unprocessable
    Entity. Checkpoint C12's contract calls for 400 on invalid shape (missing field,
    non-positive amount, malformed timestamp, invalid enum value) -- this handler is the one
    place that mapping happens, application-wide, rather than re-validating by hand in every
    route."""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST, content={"detail": jsonable_encoder(exc.errors())}
    )
