"""Seed demo mandates (Checkpoint C14). One-off local script, run manually -- NOT part of the
locked pipeline, NOT an API endpoint, and NOT used by the eval harness (which materializes its
own mandates per case via eval/dataset_loader.py's persist_case_mandate).

Inserts a handful of realistic mandates directly via the real SQLAlchemy models and a real
Alembic-migrated Postgres connection, varying category across the broadened mandate-category
taxonomy locked in docs/IMPLEMENTATION-BASELINE.md §3 (groceries, household essentials, bills,
fuel, house help, telephone) -- so the frontend's "Simulate Transaction" screen has more than
one mandate shape to demo against. `created_at` is left to its DB default (real insert time):
unlike eval fixtures, these mandates are meant to be used live, in the same session they're
created in, not backdated for velocity-signal backtesting.

Usage: from backend/ (so app.config's .env resolution and DATABASE_URL apply normally):
    ./.venv/Scripts/python.exe ../scripts/seed_demo_mandates.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.db import models  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

DEMO_MANDATES = [
    dict(
        purpose="weekly household groceries",
        budget=8000,
        period_days=7,
        allowed_categories=["groceries", "household essentials"],
    ),
    dict(
        purpose="monthly utility bill payments",
        budget=15000,
        period_days=30,
        allowed_categories=["bills", "telephone"],
    ),
    dict(
        purpose="monthly fuel and commute expenses",
        budget=6000,
        period_days=30,
        allowed_categories=["fuel"],
    ),
    dict(
        purpose="monthly house help and domestic staff wages",
        budget=12000,
        period_days=30,
        allowed_categories=["house help", "household essentials"],
    ),
]


def main() -> None:
    session = SessionLocal()
    try:
        rows = [models.Mandate(**fields) for fields in DEMO_MANDATES]
        session.add_all(rows)
        session.commit()
        for row in rows:
            print(f"{row.id}  {row.purpose!r}  budget={row.budget} period_days={row.period_days} "
                  f"allowed_categories={row.allowed_categories}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
