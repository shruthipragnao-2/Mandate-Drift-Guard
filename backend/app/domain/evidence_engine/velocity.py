"""Spend velocity signal (Checkpoint C7+C8).

Decision 9 (docs/IMPLEMENTATION-BASELINE.md, 2026-09-02):
    expected_fraction = days_elapsed_in_period / period_days   (floor days_elapsed at 1)
    actual_fraction   = spend_so_far_in_period / budget
    velocity_ratio    = actual_fraction / expected_fraction
    Bands: normal (ratio <= 1.3), elevated (1.3 < ratio <= 2.0), critical (ratio > 2.0)

[INFERRED, not settled by Decision 9]: "days elapsed in period" needs a period-start anchor,
which the schema doesn't carry as a separate field. This implementation anchors the (single,
non-rolling) period at `mandate.created_at` -- period rollover/reset after the first
`period_days` window is NOT implemented (no spec document addresses renewal semantics at all).

`[OPEN — must be resolved before M5 dataset generation begins]` (docs/IMPLEMENTATION-BASELINE.md
§18, appended 2026-09-02): for a mandate evaluated in its second, third, or Nth cycle,
`expected_fraction` keeps growing past 1.0 rather than resetting, so the velocity signal goes
numb regardless of real spend -- a real risk to eval-design's slow-drift narrative, which is
specifically about sustained drift over time. Not fixed here; needs an actual design decision
(rolling-window reset? multiple mandate-period instances? something else?) not yet made.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.config import EVIDENCE_ENGINE_THRESHOLDS, EvidenceEngineThresholds
from app.domain.evidence_engine.types import MandateLike, TransactionLike

VELOCITY_BANDS = ("normal", "elevated", "critical")


@dataclass(frozen=True)
class VelocityResult:
    band: str
    ratio: float | None
    expected_fraction: float | None
    actual_fraction: float
    spend_so_far: float
    days_elapsed: int | None
    cold_start: bool


def _band_for_ratio(ratio: float, config: EvidenceEngineThresholds) -> str:
    if ratio <= config.velocity_normal_max:
        return "normal"
    if ratio <= config.velocity_elevated_max:
        return "elevated"
    return "critical"


def compute_velocity(
    mandate: MandateLike,
    transactions_in_window: Sequence[TransactionLike],
    *,
    config: EvidenceEngineThresholds = EVIDENCE_ENGINE_THRESHOLDS,
) -> VelocityResult:
    """Cold-start (failure fixture #5, eval-design): an empty window has no transaction
    timestamp to anchor "days elapsed" on, so `ratio` genuinely can't be computed -- not just
    "computes to zero". Per architecture's own framing (cold-start "must not crash", "a
    conservative default, not a silent skip"), this returns band="elevated": the smallest band
    that still crosses the threshold check (domain/pipeline.py), deliberately not "normal"
    (which would silently wave an unevaluated mandate through) and not "critical" (which would
    guarantee-trigger on literally every brand-new mandate's first transaction, carrying no
    real signal and polluting eval metrics with a forced case).
    """
    spend_so_far = float(sum(t.amount for t in transactions_in_window))

    if not transactions_in_window:
        return VelocityResult(
            band="elevated",
            ratio=None,
            expected_fraction=None,
            actual_fraction=0.0,
            spend_so_far=0.0,
            days_elapsed=None,
            cold_start=True,
        )

    as_of = max(t.occurred_at for t in transactions_in_window)
    # TODO: no period-renewal logic -- expected_fraction grows unbounded past the mandate's
    # first period_days window. See docs/IMPLEMENTATION-BASELINE.md §18's
    # "[OPEN — must be resolved before M5 dataset generation begins]" note.
    days_elapsed = max(1, (as_of - mandate.created_at).days)
    expected_fraction = days_elapsed / mandate.period_days
    actual_fraction = spend_so_far / float(mandate.budget)
    ratio = actual_fraction / expected_fraction

    return VelocityResult(
        band=_band_for_ratio(ratio, config),
        ratio=ratio,
        expected_fraction=expected_fraction,
        actual_fraction=actual_fraction,
        spend_so_far=spend_so_far,
        days_elapsed=days_elapsed,
        cold_start=False,
    )
