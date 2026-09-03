"""Unit tests for the rules-only baseline gate (Checkpoint C11, eval-design.md §5). Pure
function, no DB, no LLM -- straightforward decision-table coverage plus the cold-start
edge case this module's docstring flags explicitly.
"""

from app.domain.evidence_engine.category_shift import CategoryShiftResult
from app.domain.evidence_engine.velocity import VelocityResult
from app.domain.rules_only_gate import decide


def _velocity(band="normal"):
    return VelocityResult(
        band=band, ratio=1.0, expected_fraction=0.5, actual_fraction=0.5, spend_so_far=100.0,
        days_elapsed=2, cold_start=False,
    )


def _category_shift(ratio=0.0, cold_start=False):
    return CategoryShiftResult(
        band="n/a", ratio=ratio, out_of_mandate_amount=0.0, total_amount=100.0, cold_start=cold_start
    )


def test_elevated_velocity_and_ratio_above_threshold_holds():
    result = decide(_velocity("elevated"), _category_shift(0.30), threshold_t=0.20)

    assert result.decision == "hold"
    assert "hold:" in result.rule_applied


def test_ratio_exactly_at_threshold_is_inclusive():
    result = decide(_velocity("elevated"), _category_shift(0.20), threshold_t=0.20)

    assert result.decision == "hold"


def test_ratio_just_below_threshold_allows():
    result = decide(_velocity("elevated"), _category_shift(0.19), threshold_t=0.20)

    assert result.decision == "allow"


def test_non_elevated_velocity_always_allows_regardless_of_category_shift():
    """The rule requires velocity == elevated exactly -- a "critical" velocity does NOT
    satisfy it, per eval-design §5's literal wording (this is a deliberately narrow baseline,
    not a bug)."""
    result = decide(_velocity("critical"), _category_shift(0.90), threshold_t=0.05)

    assert result.decision == "allow"


def test_normal_velocity_allows_even_with_high_category_shift():
    result = decide(_velocity("normal"), _category_shift(0.90), threshold_t=0.05)

    assert result.decision == "allow"


def test_elevated_velocity_alone_allows_if_category_shift_below_threshold():
    result = decide(_velocity("elevated"), _category_shift(0.0), threshold_t=0.05)

    assert result.decision == "allow"


def test_cold_start_category_shift_ratio_none_does_not_crash():
    """A None ratio (cold-start) is treated as not meeting the threshold, not a crash --
    this module's own documented [IMPL DETAIL]."""
    result = decide(_velocity("elevated"), _category_shift(None, cold_start=True), threshold_t=0.05)

    assert result.decision == "allow"
