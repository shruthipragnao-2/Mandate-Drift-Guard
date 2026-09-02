"""Category-shift signal (Checkpoint C7+C8).

Decision 10 (docs/IMPLEMENTATION-BASELINE.md, 2026-09-02):
    out_of_mandate_ratio = (sum of amount for transactions in the window whose category is
                             NOT IN mandate.allowed_categories)
                            / (sum of amount for all transactions in the window)
    Bands: none (ratio <= 0.05), minor (0.05 < ratio <= 0.20),
           significant (0.20 < ratio <= 0.45), severe (ratio > 0.45)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.config import EVIDENCE_ENGINE_THRESHOLDS, EvidenceEngineThresholds
from app.domain.evidence_engine.types import MandateLike, TransactionLike

CATEGORY_SHIFT_BANDS = ("none", "minor", "significant", "severe")


@dataclass(frozen=True)
class CategoryShiftResult:
    band: str
    ratio: float | None
    out_of_mandate_amount: float
    total_amount: float
    cold_start: bool


def _band_for_ratio(ratio: float, config: EvidenceEngineThresholds) -> str:
    if ratio <= config.category_shift_none_max:
        return "none"
    if ratio <= config.category_shift_minor_max:
        return "minor"
    if ratio <= config.category_shift_significant_max:
        return "significant"
    return "severe"


def compute_category_shift(
    mandate: MandateLike,
    transactions_in_window: Sequence[TransactionLike],
    *,
    config: EvidenceEngineThresholds = EVIDENCE_ENGINE_THRESHOLDS,
) -> CategoryShiftResult:
    """Cold-start: an empty window (or a window that sums to zero spend) makes the ratio a
    genuine 0/0, not evidence of "no drift" -- zero data is not the same claim as zero shift.
    Returns band="minor" (the same "smallest band that still triggers a threshold crossing,
    not a silent 'none'" reasoning as velocity.py's cold-start branch)."""
    allowed = set(mandate.allowed_categories)
    total_amount = float(sum(t.amount for t in transactions_in_window))

    if not transactions_in_window or total_amount == 0:
        return CategoryShiftResult(
            band="minor", ratio=None, out_of_mandate_amount=0.0, total_amount=0.0, cold_start=True
        )

    out_of_mandate_amount = float(
        sum(t.amount for t in transactions_in_window if t.category not in allowed)
    )
    ratio = out_of_mandate_amount / total_amount

    return CategoryShiftResult(
        band=_band_for_ratio(ratio, config),
        ratio=ratio,
        out_of_mandate_amount=out_of_mandate_amount,
        total_amount=total_amount,
        cold_start=False,
    )
