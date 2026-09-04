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
    route.

    Red-team finding RT-C1-007: each entry in `exc.errors()` echoes the rejected value back
    under `input`, and `jsonable_encoder` cannot serialize `Infinity`/`NaN`
    ("Out of range float values are not JSON compliant"). So a request carrying a non-finite
    number made THIS HANDLER raise while reporting the error, turning a correctly-detected
    400 into a 500 -- the validator had already done its job. Any validation failure whose
    offending input is non-JSON-serializable hit it, so it could not be fixed by validating
    harder upstream; the reporting path itself was the bug.

    `input` is dropped rather than coerced. It is attacker-controlled content being reflected
    straight back into a response, it is not needed to tell a client which field was wrong
    (`loc` and `msg` carry that), and echoing it is the only reason this handler could fail.
    `ctx` is stringified because it can hold a live exception object.
    """
    safe_errors = [
        {
            key: (str(value) if key == "ctx" else value)
            for key, value in error.items()
            if key != "input"
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST, content={"detail": jsonable_encoder(safe_errors)}
    )
