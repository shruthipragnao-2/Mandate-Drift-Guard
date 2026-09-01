"""FastAPI application entrypoint.

Checkpoint C5 / milestone M0 scope: app startup, config loading, logging, and the health
endpoint only. Route modules for mandates/transactions/cases and all pipeline logic
(evidence engine, LLM client, policy gate) are added in later milestones (M1-M4) per
docs/IMPLEMENTATION-PLAN.md §Q.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
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
