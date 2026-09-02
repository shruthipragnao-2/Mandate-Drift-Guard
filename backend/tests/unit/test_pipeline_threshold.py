"""Unit tests for the threshold-check integration point (Checkpoint C7+C8). No DB dependency.

This is Plan §D step 3's decision only -- crosses threshold (build packet, invoke layer ②) vs.
immediate ALLOW with no packet. Not the full pipeline orchestrator (M4).
"""

from app.domain.evidence_engine.category_shift import CategoryShiftResult
from app.domain.evidence_engine.clustering import ClusteringResult
from app.domain.evidence_engine.velocity import VelocityResult
from app.domain.pipeline import check_threshold

_NORMAL_VELOCITY = VelocityResult(
    band="normal", ratio=0.5, expected_fraction=0.2, actual_fraction=0.1, spend_so_far=100.0, days_elapsed=2, cold_start=False
)
_NORMAL_CATEGORY_SHIFT = CategoryShiftResult(
    band="none", ratio=0.0, out_of_mandate_amount=0.0, total_amount=100.0, cold_start=False
)
_NORMAL_CLUSTERING = ClusteringResult(band="normal", ratio=0.2, max_window_count=1, total_count=5, cold_start=False)


def test_all_normal_does_not_cross_threshold():
    result = check_threshold(_NORMAL_VELOCITY, _NORMAL_CATEGORY_SHIFT, _NORMAL_CLUSTERING)

    assert result.crossed is False
    assert result.triggering_signals == ()


def test_elevated_velocity_alone_crosses_threshold():
    velocity = VelocityResult(
        band="elevated", ratio=1.5, expected_fraction=0.2, actual_fraction=0.3, spend_so_far=300.0, days_elapsed=2, cold_start=False
    )

    result = check_threshold(velocity, _NORMAL_CATEGORY_SHIFT, _NORMAL_CLUSTERING)

    assert result.crossed is True
    assert result.triggering_signals == ("spend_velocity",)


def test_minor_category_shift_alone_crosses_threshold():
    category_shift = CategoryShiftResult(
        band="minor", ratio=0.1, out_of_mandate_amount=10.0, total_amount=100.0, cold_start=False
    )

    result = check_threshold(_NORMAL_VELOCITY, category_shift, _NORMAL_CLUSTERING)

    assert result.crossed is True
    assert result.triggering_signals == ("category_shift",)


def test_clustered_alone_crosses_threshold():
    clustering = ClusteringResult(band="clustered", ratio=0.5, max_window_count=3, total_count=6, cold_start=False)

    result = check_threshold(_NORMAL_VELOCITY, _NORMAL_CATEGORY_SHIFT, clustering)

    assert result.crossed is True
    assert result.triggering_signals == ("clustering",)


def test_multiple_triggering_signals_all_listed():
    velocity = VelocityResult(
        band="critical", ratio=3.0, expected_fraction=0.2, actual_fraction=0.6, spend_so_far=600.0, days_elapsed=2, cold_start=False
    )
    category_shift = CategoryShiftResult(
        band="severe", ratio=0.9, out_of_mandate_amount=90.0, total_amount=100.0, cold_start=False
    )

    result = check_threshold(velocity, category_shift, _NORMAL_CLUSTERING)

    assert result.crossed is True
    assert result.triggering_signals == ("spend_velocity", "category_shift")


def test_cold_start_velocity_crosses_threshold():
    """Cold-start's documented "elevated" default (velocity.py) is deliberately not "normal",
    so it must still cross the threshold -- confirming that design choice actually has the
    intended downstream effect, not just an isolated unit-level assertion."""
    cold_start_velocity = VelocityResult(
        band="elevated", ratio=None, expected_fraction=None, actual_fraction=0.0, spend_so_far=0.0, days_elapsed=None, cold_start=True
    )

    result = check_threshold(cold_start_velocity, _NORMAL_CATEGORY_SHIFT, _NORMAL_CLUSTERING)

    assert result.crossed is True
    assert result.triggering_signals == ("spend_velocity",)
