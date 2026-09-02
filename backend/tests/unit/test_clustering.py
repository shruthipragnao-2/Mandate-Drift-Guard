"""Unit tests for the clustering signal (Decisions 11-12, Checkpoint C7+C8). No DB dependency."""

from datetime import datetime, timedelta, timezone

from app.config import EVIDENCE_ENGINE_THRESHOLDS, EvidenceEngineThresholds
from app.domain.evidence_engine.clustering import _band_for_ratio, compute_clustering

# ---------------------------------------------------------------------------
# Boundary tests on the banding helper directly.
# ---------------------------------------------------------------------------


def test_band_at_normal_upper_boundary_is_normal():
    assert _band_for_ratio(0.4, EVIDENCE_ENGINE_THRESHOLDS) == "normal"


def test_band_just_above_normal_boundary_is_clustered():
    assert _band_for_ratio(0.4 + 1e-9, EVIDENCE_ENGINE_THRESHOLDS) == "clustered"


def test_band_at_clustered_upper_boundary_is_clustered():
    assert _band_for_ratio(0.7, EVIDENCE_ENGINE_THRESHOLDS) == "clustered"


def test_band_just_above_clustered_boundary_is_highly_clustered():
    assert _band_for_ratio(0.7 + 1e-9, EVIDENCE_ENGINE_THRESHOLDS) == "highly_clustered"


def test_band_at_one_is_highly_clustered():
    assert _band_for_ratio(1.0, EVIDENCE_ENGINE_THRESHOLDS) == "highly_clustered"


# ---------------------------------------------------------------------------
# Golden-value tests -- full compute_clustering() through hand-constructed timestamp streams.
# ---------------------------------------------------------------------------


def test_golden_value_normal_evenly_spread(mandate_factory, transaction_factory):
    mandate = mandate_factory()
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    # 5 transactions, one per day -- no 24h sub-window contains more than 1.
    txns = [transaction_factory(occurred_at=base + timedelta(days=i)) for i in range(5)]

    result = compute_clustering(mandate, txns)

    assert result.total_count == 5
    assert result.max_window_count == 1
    assert result.ratio == 0.2
    assert result.band == "normal"
    assert result.cold_start is False


def test_golden_value_highly_clustered_same_day_burst(mandate_factory, transaction_factory):
    mandate = mandate_factory()
    base = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    # 4 of 5 transactions land within a few hours of each other; 1 is a week later.
    txns = [
        transaction_factory(occurred_at=base),
        transaction_factory(occurred_at=base + timedelta(hours=2)),
        transaction_factory(occurred_at=base + timedelta(hours=4)),
        transaction_factory(occurred_at=base + timedelta(hours=6)),
        transaction_factory(occurred_at=base + timedelta(days=7)),
    ]

    result = compute_clustering(mandate, txns)

    assert result.total_count == 5
    assert result.max_window_count == 4
    assert result.ratio == 0.8
    assert result.band == "highly_clustered"


def test_golden_value_clustered_partial_overlap(mandate_factory, transaction_factory):
    mandate = mandate_factory()
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    # 3 of 5 fall within a 24h window; the other 2 are spread out.
    txns = [
        transaction_factory(occurred_at=base),
        transaction_factory(occurred_at=base + timedelta(hours=10)),
        transaction_factory(occurred_at=base + timedelta(hours=20)),
        transaction_factory(occurred_at=base + timedelta(days=5)),
        transaction_factory(occurred_at=base + timedelta(days=10)),
    ]

    result = compute_clustering(mandate, txns)

    assert result.max_window_count == 3
    assert result.ratio == 0.6
    assert result.band == "clustered"


def test_window_boundary_is_exclusive_at_exactly_24_hours(mandate_factory, transaction_factory):
    """A transaction exactly 24h after the window start must NOT count as still inside that
    window (`>= _WINDOW` excludes it in the sliding-window implementation)."""
    mandate = mandate_factory()
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    txns = [
        transaction_factory(occurred_at=base),
        transaction_factory(occurred_at=base + timedelta(hours=24)),
    ]

    result = compute_clustering(mandate, txns)

    assert result.max_window_count == 1
    assert result.ratio == 0.5
    assert result.band == "clustered"  # 0.5 > clustering_normal_max (0.4)


# ---------------------------------------------------------------------------
# Cold-start (failure fixture #5)
# ---------------------------------------------------------------------------


def test_cold_start_empty_window_does_not_crash(mandate_factory):
    mandate = mandate_factory()

    result = compute_clustering(mandate, [])

    assert result.cold_start is True
    assert result.ratio is None
    assert result.total_count == 0
    assert result.band == "clustered"  # documented conservative default, see clustering.py


def test_single_transaction_is_normal_band_not_cold_start(mandate_factory, transaction_factory):
    """Decision 12 (2026-09-02): N == 1 is distinct from both the cold-start (N == 0) branch
    and the general N >= 2 formula. A single transaction trivially fills its own 24h window
    (burst_ratio == 1.0 by construction), which would otherwise force every mandate's very
    first transaction to cross the threshold regardless of real drift. `ratio` is still
    reported for observability, but `band` is forced to "normal" rather than run through
    `_band_for_ratio`."""
    mandate = mandate_factory()
    txn = transaction_factory(occurred_at=datetime(2026, 8, 1, tzinfo=timezone.utc))

    result = compute_clustering(mandate, [txn])

    assert result.cold_start is False
    assert result.ratio == 1.0
    assert result.max_window_count == 1
    assert result.total_count == 1
    assert result.band == "normal"


def test_single_transaction_band_is_normal_regardless_of_thresholds(mandate_factory, transaction_factory):
    """Decision 12: the N == 1 branch returns band="normal" directly -- it does NOT go
    through `_band_for_ratio`. Proven by configuring thresholds where ratio=1.0 would never
    be "normal" under the general formula (clustering_normal_max=0.0), and confirming the
    single-transaction result is still "normal" regardless."""
    mandate = mandate_factory()
    txn = transaction_factory(occurred_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    hostile_config = EvidenceEngineThresholds(clustering_normal_max=0.0, clustering_clustered_max=0.0)

    # Sanity check: under the general formula, ratio=1.0 with these thresholds would be
    # "highly_clustered", confirming the config alone can't explain a "normal" result below.
    assert _band_for_ratio(1.0, hostile_config) == "highly_clustered"

    result = compute_clustering(mandate, [txn], config=hostile_config)

    assert result.ratio == 1.0
    assert result.band == "normal"
