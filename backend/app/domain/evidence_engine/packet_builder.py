"""Evidence packet builder (Checkpoint C7+C8).

Assembles the object layer ① passes to layer ② from the three signal results, matching the
exact locked schema shape in docs/IMPLEMENTATION-BASELINE.md §4:
    {"mandate": {...}, "signals": {...}, "trajectory": {...}}

[INFERRED, not settled by any source document]: the locked example only shows two of the
three signal identities as flat keys under "signals" (spend_velocity, category_shift) plus one
numeric (budget_utilization) -- it predates/abbreviates the third (clustering). This builder
adds a "clustering" key so all three `[LOCKED]` signal identities (baseline §3) are actually
represented, reading the example as illustrative of *shape*, not a closed, exhaustive key set.

[INFERRED, not settled anywhere]: the brief's "trajectory" sub-object is given only as
`{"historical_distribution": "...", "current_distribution": "..."}` -- an ellipsis placeholder,
no format specified. This builder represents each as a per-category amount breakdown:
"historical" = the window excluding the most recent (triggering) transaction, "current" = the
full window including it. This is a design choice, not a spec requirement.
"""

from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel, ConfigDict

from app.domain.evidence_engine.category_shift import CategoryShiftResult
from app.domain.evidence_engine.clustering import ClusteringResult
from app.domain.evidence_engine.types import MandateLike, TransactionLike
from app.domain.evidence_engine.velocity import VelocityResult

_OTHER_CATEGORY = "other"


class MandateSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    purpose: str
    budget: float
    period_days: int
    allowed_categories: list[str]


class SignalSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    budget_utilization: float
    spend_velocity: str
    category_shift: str
    clustering: str


class TrajectorySummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    historical_distribution: dict[str, float]
    current_distribution: dict[str, float]


class EvidencePacket(BaseModel):
    """Structural safety property [LOCKED, architecture §14, baseline §4]: no field on this
    model may carry arbitrary merchant-supplied free text into the LLM's instruction context.
    `mandate.purpose` is the one free-text field here, and it originates from the
    consumer-granted mandate, not the (untrusted) merchant/transaction side -- the exact
    distinction eval-design failure case #4 draws. Every category label anywhere else in this
    model is constrained to `mandate.allowed_categories` plus the literal bucket "other"; no
    raw `transaction.category` string is ever embedded verbatim (see `_bounded_category`).
    """

    model_config = ConfigDict(frozen=True)

    mandate: MandateSummary
    signals: SignalSummary
    trajectory: TrajectorySummary


def _bounded_category(category: str, allowed_categories: set[str]) -> str:
    """Never returns the raw input string unless it's already a known, mandate-controlled
    value -- the structural mechanism (not a filter/blocklist) that keeps arbitrary
    transaction-supplied text out of the packet."""
    return category if category in allowed_categories else _OTHER_CATEGORY


def _category_distribution(
    transactions: Sequence[TransactionLike], allowed_categories: set[str]
) -> dict[str, float]:
    distribution: dict[str, float] = {}
    for txn in transactions:
        key = _bounded_category(txn.category, allowed_categories)
        distribution[key] = distribution.get(key, 0.0) + float(txn.amount)
    return distribution


def build_evidence_packet(
    mandate: MandateLike,
    transactions_in_window: Sequence[TransactionLike],
    velocity_result: VelocityResult,
    category_shift_result: CategoryShiftResult,
    clustering_result: ClusteringResult,
) -> EvidencePacket:
    allowed = set(mandate.allowed_categories)
    ordered = sorted(transactions_in_window, key=lambda t: t.occurred_at)
    historical = ordered[:-1] if ordered else []

    return EvidencePacket(
        mandate=MandateSummary(
            purpose=mandate.purpose,
            budget=float(mandate.budget),
            period_days=mandate.period_days,
            allowed_categories=list(mandate.allowed_categories),
        ),
        signals=SignalSummary(
            budget_utilization=velocity_result.actual_fraction,
            spend_velocity=velocity_result.band,
            category_shift=category_shift_result.band,
            clustering=clustering_result.band,
        ),
        trajectory=TrajectorySummary(
            historical_distribution=_category_distribution(historical, allowed),
            current_distribution=_category_distribution(ordered, allowed),
        ),
    )
