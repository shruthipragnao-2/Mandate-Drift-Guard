"""Clustering signal (Checkpoint C7+C8).

Decision 11 (docs/IMPLEMENTATION-BASELINE.md, 2026-09-02):
    burst_ratio = (max transaction count in any rolling 24-hour sub-window within the
                    analysis window) / (total transaction count in the analysis window)
    Bands: normal (ratio <= 0.4), clustered (0.4 < ratio <= 0.7), highly_clustered (ratio > 0.7)

Deliberately NOT a real clustering algorithm (no k-means/DBSCAN) -- a pure, deterministic,
reproducible count, per Decision 11's own explicit framing and architecture's requirement that
layer ① stay side-effect-free and trivially unit-testable.

Decision 12 (docs/IMPLEMENTATION-BASELINE.md, 2026-09-02) refines Decision 11 for the N == 1
case: see the `N == 1` note in `compute_clustering`'s docstring below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Sequence

from app.config import EVIDENCE_ENGINE_THRESHOLDS, EvidenceEngineThresholds
from app.domain.evidence_engine.types import MandateLike, TransactionLike

CLUSTERING_BANDS = ("normal", "clustered", "highly_clustered")

_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class ClusteringResult:
    band: str
    ratio: float | None
    max_window_count: int
    total_count: int
    cold_start: bool


def _band_for_ratio(ratio: float, config: EvidenceEngineThresholds) -> str:
    if ratio <= config.clustering_normal_max:
        return "normal"
    if ratio <= config.clustering_clustered_max:
        return "clustered"
    return "highly_clustered"


def compute_clustering(
    mandate: MandateLike,
    transactions_in_window: Sequence[TransactionLike],
    *,
    config: EvidenceEngineThresholds = EVIDENCE_ENGINE_THRESHOLDS,
) -> ClusteringResult:
    """`mandate` is unused by this signal's formula -- kept only for signature parity with
    `compute_velocity`/`compute_category_shift` (docs/IMPLEMENTATION-PLAN.md §F's uniform
    `compute_<signal>(mandate, transactions_in_window)` shape) so callers can invoke all three
    signals identically without special-casing this one.

    Cold-start (total_count == 0): no transactions to cluster at all -- a genuine 0/0. Returns
    band="clustered" (the same "smallest triggering band, not a silent 'normal'" reasoning as
    the other two signals).

    N == 1 (Decision 12, docs/IMPLEMENTATION-BASELINE.md, 2026-09-02): distinct from
    cold-start -- this is one genuine data point, not zero data. But burst_ratio's formula
    makes a single transaction always evaluate to 1.0 by construction (it trivially fills its
    own 24h window), which -- combined with check_threshold's "any non-normal signal
    crosses" rule -- would force every mandate's very first transaction to trigger an LLM
    call regardless of any real drift, undercutting eval-design §18's expectation that most
    cases trigger zero LLM calls. Clustering is undefined with nothing yet to cluster
    against, so this is a genuine boundary condition of the signal, not a workaround hiding a
    design flaw: `ratio` is still computed and reported (1.0, for observability), but `band`
    is forced to "normal" rather than run through `_band_for_ratio`.
    """
    total_count = len(transactions_in_window)
    if total_count == 0:
        return ClusteringResult(
            band="clustered", ratio=None, max_window_count=0, total_count=0, cold_start=True
        )
    if total_count == 1:
        return ClusteringResult(band="normal", ratio=1.0, max_window_count=1, total_count=1, cold_start=False)

    timestamps = sorted(t.occurred_at for t in transactions_in_window)
    max_window_count = 1
    left = 0
    for right in range(len(timestamps)):
        while timestamps[right] - timestamps[left] >= _WINDOW:
            left += 1
        max_window_count = max(max_window_count, right - left + 1)

    ratio = max_window_count / total_count

    return ClusteringResult(
        band=_band_for_ratio(ratio, config),
        ratio=ratio,
        max_window_count=max_window_count,
        total_count=total_count,
        cold_start=False,
    )
