"""Shared fixtures for evidence-engine unit tests -- no DB, no SQLAlchemy models. Plain
dataclasses satisfying `app.domain.evidence_engine.types.MandateLike`/`TransactionLike` via
structural typing, matching how the eval harness (a later milestone) will construct inputs
from fixture JSON rather than live ORM rows.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest


@dataclass
class FakeMandate:
    budget: float
    period_days: int
    allowed_categories: list[str]
    created_at: datetime
    purpose: str = "weekly household groceries"


@dataclass
class FakeTransaction:
    amount: float
    category: str
    occurred_at: datetime


@pytest.fixture
def mandate_factory():
    def _make(**overrides):
        defaults = dict(
            budget=8000.0,
            period_days=7,
            allowed_categories=["groceries", "household essentials"],
            created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        defaults.update(overrides)
        return FakeMandate(**defaults)

    return _make


@pytest.fixture
def transaction_factory():
    def _make(**overrides):
        defaults = dict(
            amount=500.0,
            category="groceries",
            occurred_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
        )
        defaults.update(overrides)
        return FakeTransaction(**defaults)

    return _make


@pytest.fixture
def evidence_packet_factory():
    from app.domain.evidence_engine.packet_builder import (
        EvidencePacket,
        MandateSummary,
        SignalSummary,
        TrajectorySummary,
    )

    def _make(**overrides):
        defaults = dict(
            mandate=MandateSummary(
                purpose="weekly household groceries",
                budget=8000.0,
                period_days=7,
                allowed_categories=["groceries", "household essentials"],
            ),
            signals=SignalSummary(
                budget_utilization=0.91,
                spend_velocity="elevated",
                category_shift="significant",
                clustering="normal",
            ),
            trajectory=TrajectorySummary(
                historical_distribution={"groceries": 700.0},
                current_distribution={"groceries": 700.0, "other": 300.0},
            ),
        )
        defaults.update(overrides)
        return EvidencePacket(**defaults)

    return _make
