"""Declarative base for the ORM layer.

No domain tables are defined yet — mandates/transactions/evidence_packets/etc. belong to
milestone M1 (docs/IMPLEMENTATION-PLAN.md §Q) and are out of scope for Checkpoint C5.
`Base.metadata` is intentionally empty; this is what makes the initial Alembic revision a
no-op ("empty-schema migration applies", M0 exit criteria).
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()
