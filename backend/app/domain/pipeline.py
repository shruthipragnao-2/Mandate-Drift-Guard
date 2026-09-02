"""Pipeline orchestration (Checkpoint C7+C8 scope: threshold-check only).

This is NOT the full orchestrator described in docs/IMPLEMENTATION-PLAN.md §D -- calling
layer ② (the semantic risk client), the policy gate, and writing DB rows are milestone M4.
This module currently answers exactly Plan §D step 3's question: given the three computed
signal results, does this transaction's evaluation cross the deterministic threshold that
requires building an evidence packet and invoking layer ②, or does it get an immediate ALLOW
with no packet at all?
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.evidence_engine.category_shift import CategoryShiftResult
from app.domain.evidence_engine.clustering import ClusteringResult
from app.domain.evidence_engine.velocity import VelocityResult


@dataclass(frozen=True)
class ThresholdCheckResult:
    crossed: bool
    triggering_signals: tuple[str, ...]


def check_threshold(
    velocity_result: VelocityResult,
    category_shift_result: CategoryShiftResult,
    clustering_result: ClusteringResult,
) -> ThresholdCheckResult:
    """[INFERRED -- not settled by Decisions 9-11 or any prior spec document]: those decisions
    fix each signal's own banding, but not a cross-signal trigger rule for "does this warrant
    a second look at all". This implements the simplest, most conservative rule available: ANY
    signal reading above its own lowest ("no drift") band is enough to cross the threshold --
    velocity != "normal", category_shift != "none", or clustering != "normal". No weighted or
    combined score is attempted; nothing in the source docs calls for one, and a single
    elevated signal warranting review is consistent with the fail-closed philosophy already
    locked elsewhere (baseline §6). Revisit if dev-set calibration (a later milestone) shows
    this over- or under-triggers -- that calibration is explicitly out of this checkpoint's
    scope.
    """
    triggering: list[str] = []
    if velocity_result.band != "normal":
        triggering.append("spend_velocity")
    if category_shift_result.band != "none":
        triggering.append("category_shift")
    if clustering_result.band != "normal":
        triggering.append("clustering")

    return ThresholdCheckResult(crossed=bool(triggering), triggering_signals=tuple(triggering))
