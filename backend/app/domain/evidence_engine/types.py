"""Structural (duck-typed) input contracts for the evidence engine.

Deliberately `typing.Protocol`, not a concrete dataclass or a dependency on
`app.db.models.Mandate`/`Transaction` -- the evidence engine must stay importable and usable
by `eval/` (a later milestone) without a DB session or SQLAlchemy at all, per the
framework-agnostic requirement in docs/IMPLEMENTATION-PLAN.md §A. Any object with matching
attributes satisfies these -- a live ORM row, a fixture-loaded dataclass, or a test double.
"""

from datetime import datetime
from typing import Protocol, Sequence


class MandateLike(Protocol):
    purpose: str
    budget: float
    period_days: int
    allowed_categories: Sequence[str]
    created_at: datetime


class TransactionLike(Protocol):
    amount: float
    category: str
    occurred_at: datetime
