"""FastAPI application entrypoint.

Checkpoint C5 / milestone M0 built app startup, config loading, logging, and the health
endpoint. Checkpoint C12 adds the ingestion and HOLD-resolution routers (routing/serialization/
auth only -- both wrap the already-built, unmodified `domain.pipeline` orchestrator).
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
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
