"""Rules-only baseline gate (Checkpoint C11, eval-design.md §5). NOT the hybrid Policy Gate
(`domain/policy_gate.py`, untouched by this checkpoint) -- a deliberately simpler,
LLM-free comparison system, run on the same test set as the hybrid pipeline so the
Drift_cases_caught_only_by_hybrid metric (eval-design §9) is an apples-to-apples comparison.

Exact rule, per eval-design.md §5, read fresh rather than paraphrased:
    IF velocity == elevated AND category_shift >= threshold_T THEN HOLD ELSE ALLOW
(BLOCK is reachable only via the same HOLD-timeout mechanism as the hybrid system --
`domain/pipeline.py`'s `check_and_apply_timeout`, unchanged -- for a fair comparison; this
module never returns BLOCK itself.)

`threshold_T` is deliberately NOT hardcoded here or in this module's default -- it is a
required argument, calibrated against the dev set only by `eval/calibrate_baseline.py` and
recorded in `eval/calibration_log.md`, so the choice is reproducible and auditable rather than
picked ad hoc.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.evidence_engine.category_shift import CategoryShiftResult
from app.domain.evidence_engine.velocity import VelocityResult


@dataclass(frozen=True)
class RulesOnlyDecisionResult:
    decision: Literal["allow", "hold"]
    rule_applied: str


def decide(
    velocity_result: VelocityResult,
    category_shift_result: CategoryShiftResult,
    *,
    threshold_t: float,
) -> RulesOnlyDecisionResult:
    """`category_shift_result.ratio` is `None` only on a cold-start window (no transactions,
    or zero total spend) -- genuinely undefined, not "zero shift". Treated as not meeting the
    threshold (`False`) rather than crashing on a `None` comparison; this is an `[IMPL DETAIL]`
    this module's docstring flags explicitly, since eval-design §5 doesn't address cold-start
    for the baseline system at all.
    """
    ratio = category_shift_result.ratio if category_shift_result.ratio is not None else 0.0
    category_shift_ok = ratio >= threshold_t

    if velocity_result.band == "elevated" and category_shift_ok:
        return RulesOnlyDecisionResult(
            decision="hold",
            rule_applied=f"hold: velocity=elevated AND category_shift_ratio={ratio}>={threshold_t}",
        )

    return RulesOnlyDecisionResult(
        decision="allow",
        rule_applied=(
            f"allow: velocity={velocity_result.band} (need elevated), "
            f"category_shift_ratio={ratio} (need >={threshold_t})"
        ),
    )
