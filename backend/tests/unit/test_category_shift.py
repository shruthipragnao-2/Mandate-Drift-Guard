"""Unit tests for the category-shift signal (Decision 10, Checkpoint C7+C8). No DB dependency."""

from datetime import datetime, timezone

from app.config import EVIDENCE_ENGINE_THRESHOLDS
from app.domain.evidence_engine.category_shift import _band_for_ratio, compute_category_shift

# ---------------------------------------------------------------------------
# Boundary tests on the banding helper directly.
# ---------------------------------------------------------------------------


def test_band_at_none_upper_boundary_is_none():
    assert _band_for_ratio(0.05, EVIDENCE_ENGINE_THRESHOLDS) == "none"


def test_band_just_above_none_boundary_is_minor():
    assert _band_for_ratio(0.05 + 1e-9, EVIDENCE_ENGINE_THRESHOLDS) == "minor"


def test_band_at_minor_upper_boundary_is_minor():
    assert _band_for_ratio(0.20, EVIDENCE_ENGINE_THRESHOLDS) == "minor"


def test_band_just_above_minor_boundary_is_significant():
    assert _band_for_ratio(0.20 + 1e-9, EVIDENCE_ENGINE_THRESHOLDS) == "significant"


def test_band_at_significant_upper_boundary_is_significant():
    assert _band_for_ratio(0.45, EVIDENCE_ENGINE_THRESHOLDS) == "significant"


def test_band_just_above_significant_boundary_is_severe():
    assert _band_for_ratio(0.45 + 1e-9, EVIDENCE_ENGINE_THRESHOLDS) == "severe"


def test_band_at_zero_is_none():
    assert _band_for_ratio(0.0, EVIDENCE_ENGINE_THRESHOLDS) == "none"


def test_band_at_one_is_severe():
    assert _band_for_ratio(1.0, EVIDENCE_ENGINE_THRESHOLDS) == "severe"


# ---------------------------------------------------------------------------
# Golden-value tests -- full compute_category_shift() through hand-constructed streams.
# ---------------------------------------------------------------------------


def test_golden_value_significant_band(mandate_factory, transaction_factory):
    mandate = mandate_factory(allowed_categories=["groceries", "household essentials"])
    txns = [
        transaction_factory(amount=700.0, category="groceries", occurred_at=datetime(2026, 8, 2, tzinfo=timezone.utc)),
        transaction_factory(amount=300.0, category="electronics", occurred_at=datetime(2026, 8, 3, tzinfo=timezone.utc)),
    ]

    result = compute_category_shift(mandate, txns)

    assert result.total_amount == 1000.0
    assert result.out_of_mandate_amount == 300.0
    assert result.ratio == 0.3
    assert result.band == "significant"
    assert result.cold_start is False


def test_golden_value_none_band_all_in_mandate(mandate_factory, transaction_factory):
    mandate = mandate_factory(allowed_categories=["groceries", "household essentials"])
    txns = [
        transaction_factory(amount=400.0, category="groceries", occurred_at=datetime(2026, 8, 2, tzinfo=timezone.utc)),
        transaction_factory(amount=200.0, category="household essentials", occurred_at=datetime(2026, 8, 3, tzinfo=timezone.utc)),
    ]

    result = compute_category_shift(mandate, txns)

    assert result.out_of_mandate_amount == 0.0
    assert result.ratio == 0.0
    assert result.band == "none"


def test_golden_value_severe_band_mostly_out_of_mandate(mandate_factory, transaction_factory):
    mandate = mandate_factory(allowed_categories=["groceries"])
    txns = [
        transaction_factory(amount=100.0, category="groceries", occurred_at=datetime(2026, 8, 2, tzinfo=timezone.utc)),
        transaction_factory(amount=900.0, category="electronics", occurred_at=datetime(2026, 8, 3, tzinfo=timezone.utc)),
    ]

    result = compute_category_shift(mandate, txns)

    assert result.ratio == 0.9
    assert result.band == "severe"


# ---------------------------------------------------------------------------
# Cold-start (failure fixture #5)
# ---------------------------------------------------------------------------


def test_cold_start_empty_window_does_not_crash(mandate_factory):
    mandate = mandate_factory()

    result = compute_category_shift(mandate, [])

    assert result.cold_start is True
    assert result.ratio is None
    assert result.total_amount == 0.0
    assert result.band == "minor"  # documented conservative default, see category_shift.py
