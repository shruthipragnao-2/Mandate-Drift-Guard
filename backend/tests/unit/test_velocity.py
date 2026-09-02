"""Unit tests for the spend velocity signal (Decision 9, Checkpoint C7+C8). No DB dependency."""

from datetime import datetime, timedelta, timezone

from app.config import EVIDENCE_ENGINE_THRESHOLDS
from app.domain.evidence_engine.velocity import _band_for_ratio, compute_velocity

# ---------------------------------------------------------------------------
# Boundary tests on the banding helper directly -- exact literal ratios, no
# division-derived floats, so these can assert exact equality without float flakiness.
# eval-design's signal_match formula requires exact bucket equality for pairing verification,
# so the boundaries themselves (not just "clearly inside a band") matter.
# ---------------------------------------------------------------------------


def test_band_at_normal_upper_boundary_is_normal():
    assert _band_for_ratio(1.3, EVIDENCE_ENGINE_THRESHOLDS) == "normal"


def test_band_just_above_normal_boundary_is_elevated():
    assert _band_for_ratio(1.3 + 1e-9, EVIDENCE_ENGINE_THRESHOLDS) == "elevated"


def test_band_below_normal_boundary_is_normal():
    assert _band_for_ratio(1.0, EVIDENCE_ENGINE_THRESHOLDS) == "normal"


def test_band_at_elevated_upper_boundary_is_elevated():
    assert _band_for_ratio(2.0, EVIDENCE_ENGINE_THRESHOLDS) == "elevated"


def test_band_just_above_elevated_boundary_is_critical():
    assert _band_for_ratio(2.0 + 1e-9, EVIDENCE_ENGINE_THRESHOLDS) == "critical"


def test_band_well_above_elevated_boundary_is_critical():
    assert _band_for_ratio(5.0, EVIDENCE_ENGINE_THRESHOLDS) == "critical"


def test_band_at_zero_is_normal():
    assert _band_for_ratio(0.0, EVIDENCE_ENGINE_THRESHOLDS) == "normal"


# ---------------------------------------------------------------------------
# Golden-value tests -- full compute_velocity() through hand-constructed transaction streams,
# exercising the formula wiring (days-elapsed floor, expected/actual fraction, division), not
# just the banding helper in isolation.
# ---------------------------------------------------------------------------


def test_golden_value_elevated_band(mandate_factory, transaction_factory):
    mandate = mandate_factory(budget=8000.0, period_days=7, created_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    txn = transaction_factory(amount=3000.0, occurred_at=datetime(2026, 8, 3, tzinfo=timezone.utc))

    result = compute_velocity(mandate, [txn])

    # days_elapsed = 2, expected_fraction = 2/7, actual_fraction = 3000/8000 = 0.375
    # ratio = 0.375 / (2/7) = 0.375 * 3.5 = 1.3125
    assert result.days_elapsed == 2
    assert result.actual_fraction == 0.375
    assert result.ratio == 1.3125
    assert result.band == "elevated"
    assert result.cold_start is False


def test_golden_value_normal_band_low_spend(mandate_factory, transaction_factory):
    mandate = mandate_factory(budget=8000.0, period_days=7, created_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    txn = transaction_factory(amount=500.0, occurred_at=datetime(2026, 8, 8, tzinfo=timezone.utc))

    result = compute_velocity(mandate, [txn])

    # days_elapsed = 7, expected_fraction = 1.0, actual_fraction = 500/8000 = 0.0625
    assert result.days_elapsed == 7
    assert result.ratio == 0.0625
    assert result.band == "normal"


def test_golden_value_critical_band_spend_spike(mandate_factory, transaction_factory):
    mandate = mandate_factory(budget=8000.0, period_days=7, created_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    txn = transaction_factory(amount=7000.0, occurred_at=datetime(2026, 8, 2, tzinfo=timezone.utc))

    result = compute_velocity(mandate, [txn])

    # days_elapsed = 1, expected_fraction = 1/7, actual_fraction = 7000/8000 = 0.875
    # ratio = 0.875 * 7 = 6.125
    assert result.days_elapsed == 1
    assert result.ratio == 6.125
    assert result.band == "critical"


def test_days_elapsed_floors_at_one_for_same_day_transaction(mandate_factory, transaction_factory):
    created_at = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    mandate = mandate_factory(budget=1000.0, period_days=7, created_at=created_at)
    # Occurs 2 hours after mandate creation -- (occurred_at - created_at).days == 0, must floor to 1.
    txn = transaction_factory(amount=100.0, occurred_at=created_at + timedelta(hours=2))

    result = compute_velocity(mandate, [txn])

    assert result.days_elapsed == 1


def test_multiple_transactions_sum_spend_so_far(mandate_factory, transaction_factory):
    mandate = mandate_factory(budget=8000.0, period_days=7, created_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    txns = [
        transaction_factory(amount=1000.0, occurred_at=datetime(2026, 8, 2, tzinfo=timezone.utc)),
        transaction_factory(amount=2000.0, occurred_at=datetime(2026, 8, 3, tzinfo=timezone.utc)),
    ]

    result = compute_velocity(mandate, txns)

    assert result.spend_so_far == 3000.0
    assert result.days_elapsed == 2  # anchored on the latest (max) occurred_at


# ---------------------------------------------------------------------------
# Cold-start (failure fixture #5)
# ---------------------------------------------------------------------------


def test_cold_start_empty_window_does_not_crash(mandate_factory):
    mandate = mandate_factory()

    result = compute_velocity(mandate, [])

    assert result.cold_start is True
    assert result.ratio is None
    assert result.days_elapsed is None
    assert result.spend_so_far == 0.0
    assert result.band == "elevated"  # documented conservative default, see velocity.py
