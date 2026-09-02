"""Stage B pairing verification (Checkpoint M5 pilot, eval-design.md §2 -- "generation is not
verification").

Runs the REAL deterministic evidence engine (backend/app/domain/evidence_engine/*) against
each candidate pair -- no mocking, no hand-calculation trusted as ground truth. Implements the
EXACT signal_match formula from eval-design.md §2:

    signal_match = (velocity_A == velocity_B)
                   AND (category_shift_bucket_A == category_shift_bucket_B)
                   AND (|spend_A - spend_B| / spend_A <= 0.05)
                   AND (|count_A - count_B| <= 1)

`clustering` is deliberately NOT part of signal_match -- eval-design.md §2 only names velocity,
category-shift bucket, and spend/count tolerance; clustering is the one signal explicitly
allowed to differ between paired members.

A pair failing signal_match is REJECTED -- logged, not shipped as-is (eval-design.md §2: "the
pair is rejected and regenerated -- it does not go in the dataset as-is").
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.domain.evidence_engine.category_shift import compute_category_shift  # noqa: E402
from app.domain.evidence_engine.clustering import compute_clustering  # noqa: E402
from app.domain.evidence_engine.velocity import compute_velocity  # noqa: E402

SPEND_TOLERANCE = 0.05
COUNT_TOLERANCE = 1

# The pilot's three candidate pairs (legitimate path, drift path). Extend this list for future
# batches -- verify_pairs.py itself doesn't hardcode a batch size.
PILOT_PAIRS: list[tuple[str, str]] = [
    (
        "fixtures/legitimate/pair_001_fast_spike_velocity_legit.json",
        "fixtures/drift/pair_001_fast_spike_velocity_drift.json",
    ),
    (
        "fixtures/legitimate/pair_002_slow_drift_category_legit.json",
        "fixtures/drift/pair_002_slow_drift_category_drift.json",
    ),
    (
        "fixtures/legitimate/pair_003_combined_signals_legit.json",
        "fixtures/drift/pair_003_combined_signals_drift.json",
    ),
]


@dataclass
class _Mandate:
    purpose: str
    budget: float
    period_days: int
    allowed_categories: list
    created_at: datetime


@dataclass
class _Transaction:
    amount: float
    category: str
    occurred_at: datetime


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_case(path: Path) -> dict:
    return json.loads(path.read_text())


def to_domain_objects(case: dict) -> tuple[_Mandate, list[_Transaction]]:
    """Adapts fixture JSON into the plain duck-typed objects
    `app.domain.evidence_engine.types.MandateLike`/`TransactionLike` expect -- the real
    signal functions, unmodified, run against these exactly as they would against ORM rows or
    eval-harness-loaded fixtures."""
    m = case["mandate"]
    mandate = _Mandate(
        purpose=m["purpose"],
        budget=m["budget"],
        period_days=m["period_days"],
        allowed_categories=m["allowed_categories"],
        created_at=_parse_dt(m["created_at"]),
    )
    transactions = [
        _Transaction(amount=t["amount"], category=t["category"], occurred_at=_parse_dt(t["occurred_at"]))
        for t in case["transactions"]
    ]
    return mandate, transactions


def compute_signals(case: dict) -> dict:
    mandate, transactions = to_domain_objects(case)
    velocity = compute_velocity(mandate, transactions)
    category_shift = compute_category_shift(mandate, transactions)
    clustering = compute_clustering(mandate, transactions)
    return {
        "velocity_band": velocity.band,
        "velocity_ratio": velocity.ratio,
        "category_shift_band": category_shift.band,
        "category_shift_ratio": category_shift.ratio,
        "clustering_band": clustering.band,
        "clustering_ratio": clustering.ratio,
        "spend": sum(t.amount for t in transactions),
        "count": len(transactions),
    }


def signal_match(signals_a: dict, signals_b: dict) -> tuple[bool, dict]:
    velocity_match = signals_a["velocity_band"] == signals_b["velocity_band"]
    category_shift_match = signals_a["category_shift_band"] == signals_b["category_shift_band"]

    spend_a = signals_a["spend"]
    spend_tolerance_ok = spend_a != 0 and abs(spend_a - signals_b["spend"]) / spend_a <= SPEND_TOLERANCE
    count_tolerance_ok = abs(signals_a["count"] - signals_b["count"]) <= COUNT_TOLERANCE

    detail = {
        "velocity_match": velocity_match,
        "category_shift_match": category_shift_match,
        "spend_tolerance_ok": spend_tolerance_ok,
        "count_tolerance_ok": count_tolerance_ok,
    }
    return all(detail.values()), detail


def verify_pair(path_a: Path, path_b: Path) -> dict:
    path_a = path_a.resolve()
    path_b = path_b.resolve()
    case_a = load_case(path_a)
    case_b = load_case(path_b)
    signals_a = compute_signals(case_a)
    signals_b = compute_signals(case_b)
    matched, detail = signal_match(signals_a, signals_b)
    return {
        "path_a": str(path_a.relative_to(REPO_ROOT)),
        "path_b": str(path_b.relative_to(REPO_ROOT)),
        "signals_a": signals_a,
        "signals_b": signals_b,
        "matched": matched,
        "detail": detail,
    }


def _print_result(result: dict) -> None:
    status = "PASS" if result["matched"] else "REJECTED"
    print(f"\n[{status}] {result['path_a']}  <->  {result['path_b']}")
    a, b = result["signals_a"], result["signals_b"]
    print(
        f"  velocity:        A={a['velocity_band']:<10} (ratio={a['velocity_ratio']})   "
        f"B={b['velocity_band']:<10} (ratio={b['velocity_ratio']})   match={result['detail']['velocity_match']}"
    )
    print(
        f"  category_shift:  A={a['category_shift_band']:<10} (ratio={a['category_shift_ratio']})   "
        f"B={b['category_shift_band']:<10} (ratio={b['category_shift_ratio']})   match={result['detail']['category_shift_match']}"
    )
    print(
        f"  clustering:      A={a['clustering_band']:<10} (ratio={a['clustering_ratio']})   "
        f"B={b['clustering_band']:<10} (ratio={b['clustering_ratio']})   (not part of signal_match)"
    )
    print(
        f"  spend:           A={a['spend']}   B={b['spend']}   "
        f"diff={abs(a['spend'] - b['spend']) / a['spend']:.4f} <= {SPEND_TOLERANCE}   "
        f"ok={result['detail']['spend_tolerance_ok']}"
    )
    print(
        f"  count:           A={a['count']}   B={b['count']}   "
        f"diff={abs(a['count'] - b['count'])} <= {COUNT_TOLERANCE}   ok={result['detail']['count_tolerance_ok']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", action="store_true", help="Verify the pilot batch's known pairs.")
    parser.add_argument(
        "paths", nargs="*",
        help="Explicit pairs of fixture file paths (A1 B1 A2 B2 ...) to verify instead of --pilot.",
    )
    args = parser.parse_args()

    if args.pilot:
        pairs = [(REPO_ROOT / a, REPO_ROOT / b) for a, b in PILOT_PAIRS]
    elif args.paths:
        if len(args.paths) % 2 != 0:
            parser.error("paths must be given in pairs (A1 B1 A2 B2 ...)")
        flat = [Path(p) for p in args.paths]
        pairs = list(zip(flat[0::2], flat[1::2]))
    else:
        parser.error("pass --pilot or explicit pairs of paths")
        return

    results = [verify_pair(a, b) for a, b in pairs]
    for result in results:
        _print_result(result)

    rejected = [r for r in results if not r["matched"]]
    print(f"\n{'=' * 70}")
    print(f"Stage B summary: {len(results)} pair(s) checked, {len(rejected)} rejected")
    if results:
        print(f"Rejection rate: {len(rejected) / len(results):.1%}")
    for r in rejected:
        print(f"  REJECTED: {r['path_a']} <-> {r['path_b']} -- {r['detail']}")

    if rejected:
        sys.exit(1)


if __name__ == "__main__":
    main()
