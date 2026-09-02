"""Policy Gate — layer ③ (Checkpoint C10).

The sole owner of the ALLOW/HOLD mapping (architecture §3, `[LOCKED]`). Never calls the LLM,
never computes evidence signals -- `decide()` is a pure function of already-computed results.
BLOCK is not decided here at all: it is only reachable later, via HOLD resolution/timeout
(milestone M4, not this checkpoint). This module does not write to any DB and is not the full
pipeline orchestrator (`domain/pipeline.py`'s `check_threshold` is a separate, earlier stage,
unmodified by this checkpoint).

Decision table implemented (docs/IMPLEMENTATION-PLAN.md §I, now fully resolved):
    threshold not crossed              -> structurally unreachable here; decide() raises
    llm_outcome.status != "success"    -> HOLD, unconditionally (fail-closed, baseline §6)
    status == "success", risk_level in {"medium", "high"}  -> HOLD
    status == "success", risk_level == "low"                -> Decision 15's bounded downgrade

Decision 15 (docs/IMPLEMENTATION-BASELINE.md, 2026-09-02): a "low" risk_level may downgrade a
triggered case to ALLOW only if ALL three hold:
    1. Exactly one signal triggered, and it is at its mildest triggering band only
       (velocity=="elevated", category_shift=="minor", or clustering=="clustered" -- never a
       more severe band, and never more than one signal).
    2. confidence >= config.confidence_floor (0.7).
    3. mandate_alignment != "low" (an internal LLM contradiction -- "low" risk but "low"
       alignment -- fails closed to HOLD, it does not get benefit of the doubt).
This is a fixed, auditable function reading the LLM's four reported fields -- the LLM never
decides ALLOW/HOLD itself (zero execution authority, baseline §5, unchanged by this
checkpoint).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.config import GatePolicyConfig, GATE_POLICY_CONFIG
from app.domain.evidence_engine.category_shift import CategoryShiftResult
from app.domain.evidence_engine.clustering import ClusteringResult
from app.domain.evidence_engine.velocity import VelocityResult
from app.domain.pipeline import ThresholdCheckResult
from app.domain.semantic_risk_client import SemanticAssessmentOutcome

# The mildest band per signal that still crosses the threshold (domain/pipeline.py's
# check_threshold) -- Decision 15 condition 1 requires the single triggering signal to be
# exactly this band, not a more severe one.
_MILDEST_TRIGGERING_BAND = {
    "spend_velocity": "elevated",
    "category_shift": "minor",
    "clustering": "clustered",
}


@dataclass(frozen=True)
class GateDecisionResult:
    decision: Literal["allow", "hold"]
    rule_applied: str
    rule_version: str


class ThresholdNotCrossedError(ValueError):
    """Raised when `decide()` is called with a `ThresholdCheckResult` where crossed=False.
    This state is structurally unreachable through the intended call path -- a case that
    never crossed the threshold gets an immediate ALLOW with no evidence packet and no layer
    ② call (Plan §D step 3), so the gate is never invoked for it at all. A caller reaching
    this branch is a programming error upstream, not a data condition to handle silently."""


def _single_mild_triggering_signal(
    threshold_check: ThresholdCheckResult,
    velocity_result: VelocityResult,
    category_shift_result: CategoryShiftResult,
    clustering_result: ClusteringResult,
) -> bool:
    if len(threshold_check.triggering_signals) != 1:
        return False

    signal_name = threshold_check.triggering_signals[0]
    band_by_signal = {
        "spend_velocity": velocity_result.band,
        "category_shift": category_shift_result.band,
        "clustering": clustering_result.band,
    }
    return band_by_signal[signal_name] == _MILDEST_TRIGGERING_BAND[signal_name]


def decide(
    threshold_check: ThresholdCheckResult,
    velocity_result: VelocityResult,
    category_shift_result: CategoryShiftResult,
    clustering_result: ClusteringResult,
    llm_outcome: SemanticAssessmentOutcome,
    *,
    config: GatePolicyConfig = GATE_POLICY_CONFIG,
) -> GateDecisionResult:
    if not threshold_check.crossed:
        raise ThresholdNotCrossedError(
            "policy_gate.decide() called with threshold_check.crossed=False -- a case that "
            "never crossed the threshold must never reach the gate at all."
        )

    if llm_outcome.status != "success":
        return GateDecisionResult(
            decision="hold",
            rule_applied=f"fail_closed: llm_status={llm_outcome.status}",
            rule_version=config.rule_version,
        )

    llm_output = llm_outcome.llm_output
    risk_level = llm_output.risk_level

    if risk_level in ("medium", "high"):
        return GateDecisionResult(
            decision="hold",
            rule_applied=f"hold: risk_level={risk_level}",
            rule_version=config.rule_version,
        )

    # risk_level == "low" from here on -- Decision 15's bounded downgrade.
    single_mild = _single_mild_triggering_signal(
        threshold_check, velocity_result, category_shift_result, clustering_result
    )
    confidence_ok = llm_output.confidence >= config.confidence_floor
    alignment_ok = llm_output.mandate_alignment != "low"

    if single_mild and confidence_ok and alignment_ok:
        (signal_name,) = threshold_check.triggering_signals
        return GateDecisionResult(
            decision="allow",
            rule_applied=(
                f"bounded_downgrade: single mild signal ({signal_name}), "
                f"confidence={llm_output.confidence}, mandate_alignment={llm_output.mandate_alignment}"
            ),
            rule_version=config.rule_version,
        )

    failed_conditions: list[str] = []
    if not single_mild:
        if len(threshold_check.triggering_signals) != 1:
            failed_conditions.append(f"multiple_signals_triggered={threshold_check.triggering_signals}")
        else:
            (signal_name,) = threshold_check.triggering_signals
            band_by_signal = {
                "spend_velocity": velocity_result.band,
                "category_shift": category_shift_result.band,
                "clustering": clustering_result.band,
            }
            failed_conditions.append(f"signal_not_mildest_band: {signal_name}={band_by_signal[signal_name]}")
    if not confidence_ok:
        failed_conditions.append(f"confidence_below_floor: {llm_output.confidence}<{config.confidence_floor}")
    if not alignment_ok:
        failed_conditions.append("mandate_alignment=low")

    return GateDecisionResult(
        decision="hold",
        rule_applied=f"hold: risk_level=low, downgrade blocked ({'; '.join(failed_conditions)})",
        rule_version=config.rule_version,
    )
