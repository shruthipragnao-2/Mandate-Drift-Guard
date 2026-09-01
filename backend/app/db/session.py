"""Engine/session foundation.

`create_engine` is lazy — no connection is opened at import time, so importing this module
(and therefore `app.main`) does not require a reachable database. Actual connectivity is
exercised the first time a session executes a query, which no route does yet in M0.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """FastAPI dependency for a request-scoped DB session. Not wired into any route yet."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
