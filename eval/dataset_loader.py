"""Shared dataset_cases loader (Checkpoint C11), used by both eval/calibrate_baseline.py and
eval/run.py so the dev/test split boundary is enforced in exactly one place, not reimplemented
per script and allowed to drift.

Hard safety guard: `load_dev_cases()` queries `split == "dev"` explicitly, then re-validates
every returned row -- raising `TestSplitAccessError`, never silently skipping -- if it ever
sees `split == "test"`. This checkpoint (and every dev-set-iteration script before C13) must
never touch the locked test set; C13's dedicated one-time run is the only sanctioned reader of
`split == "test"` rows, and it will not use this function.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.db.models import DatasetCase  # noqa: E402


class TestSplitAccessError(RuntimeError):
    """Raised when a dev-set-only script would otherwise load a dataset_cases row with
    split == "test". Only Checkpoint C13's dedicated locked-test-set run may touch that
    split -- everything before it (calibration, dev-set eval iteration) must stay on "dev"."""


@dataclass(frozen=True)
class _Mandate:
    purpose: str
    budget: float
    period_days: int
    allowed_categories: list
    created_at: object  # datetime, kept loosely typed to match generate_full_dataset._Mandate


@dataclass(frozen=True)
class _Transaction:
    merchant: str
    amount: float
    category: str
    occurred_at: object


@dataclass(frozen=True)
class CaseRecord:
    """One dataset_cases row, joined with its fixture JSON's mandate/transaction stream."""

    id: str
    split: str
    category: str  # "legitimate" | "drift" | "ambiguous"
    drift_type: str  # "fast_spike" | "slow_drift" | "n_a"
    ground_truth_label: str
    fixture_path: str
    mandate: _Mandate
    transactions: list[_Transaction]


def _parse_dt(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load_fixture(fixture_path: str) -> dict:
    return json.loads((REPO_ROOT / fixture_path).read_text())


def _to_case_record(row: DatasetCase) -> CaseRecord:
    fixture = _load_fixture(row.fixture_path)
    m = fixture["mandate"]
    mandate = _Mandate(
        purpose=m["purpose"],
        budget=m["budget"],
        period_days=m["period_days"],
        allowed_categories=m["allowed_categories"],
        created_at=_parse_dt(m["created_at"]),
    )
    transactions = [
        _Transaction(
            merchant=t["merchant"],
            amount=t["amount"],
            category=t["category"],
            occurred_at=_parse_dt(t["occurred_at"]),
        )
        for t in fixture["transactions"]
    ]
    return CaseRecord(
        id=str(row.id),
        split=row.split,
        category=row.category,
        drift_type=row.drift_type,
        ground_truth_label=row.ground_truth_label,
        fixture_path=row.fixture_path,
        mandate=mandate,
        transactions=transactions,
    )


def _assert_no_test_split(rows: Sequence[DatasetCase]) -> None:
    """Isolated on purpose -- unit-testable with plain fake rows, no DB required, so the guard
    itself has a fast, direct test rather than only an end-to-end one."""
    for row in rows:
        if row.split == "test":
            raise TestSplitAccessError(
                f"dataset_cases row {row.id} has split='test' -- refusing to load it here. "
                "Only Checkpoint C13's locked-test-set run may read split='test' rows."
            )


def load_dev_cases(session) -> list[CaseRecord]:
    """The ONLY sanctioned way eval/calibrate_baseline.py and eval/run.py may read
    dataset_cases in this checkpoint."""
    rows = session.query(DatasetCase).filter(DatasetCase.split == "dev").all()
    _assert_no_test_split(rows)  # defense in depth against a future query-filter bug
    return [_to_case_record(row) for row in rows]
