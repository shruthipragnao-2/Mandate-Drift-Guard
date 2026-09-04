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

from app.db import models  # noqa: E402
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


def load_test_cases(session, *, confirm: bool) -> list[CaseRecord]:
    """The ONLY sanctioned way to read `dataset_cases` rows with split == "test" -- the locked
    test set every other script in this project must never touch. Mirrors `load_dev_cases()`'s
    shape but inverted, and is deliberately kept separate from it rather than added as a
    parameter to the same function, so the dev-set code path above is untouched by this one's
    existence.

    `confirm` is a required keyword-only argument with no default specifically so this cannot
    be invoked by accident -- via positional-argument reordering, a copied call site, or a
    variable that happens to be truthy. It must be the literal `True` typed at the call site by
    a human-reviewed script. As of Checkpoint C13, that is exactly one call site:
    eval/run_locked_test.py's dedicated, one-time locked-test-set run.

    Two ways to fail closed rather than proceed with a bad locked-test-set run:
    - `confirm is not True`: refuses outright, even for a merely truthy value (`1`, `"yes"`) --
      it must be the literal boolean, never a stand-in that happens to pass a truthiness check.
    - The query returns zero rows: refuses rather than silently reporting an empty, vacuous run
      as if it were a real result -- the exact failure mode `dataset_cases` was found in during
      C13 prep on 2026-09-04 (wiped empty by an unmarked migration round-trip test), before it
      was repopulated. An empty table must never be mistaken for "zero test cases by design."
    """
    if confirm is not True:
        raise TestSplitAccessError(
            "load_test_cases() called without confirm=True (the literal boolean) -- refusing "
            "to read the locked test set. This function exists for exactly one call site: "
            "Checkpoint C13's dedicated, one-time locked-test-set run."
        )

    rows = session.query(DatasetCase).filter(DatasetCase.split == "test").all()
    if not rows:
        raise TestSplitAccessError(
            "load_test_cases() query returned zero split='test' rows -- refusing to proceed "
            "with a vacuous locked-test-set run. Confirm dataset_cases is populated (see "
            "eval/populate_dataset_cases.py) before retrying."
        )
    return [_to_case_record(row) for row in rows]


def upsert_log_section(path: Path, marker: str, content: str) -> None:
    """Replace the named section in `path` if one already exists, else append it -- shared by
    eval/calibrate_baseline.py's sweep-table writer and eval/run.py's dev-set-run-summary
    writer so BOTH can maintain their own permanent, always-current section of
    eval/calibration_log.md without one script's re-run silently wiping the other's content
    (the file used to be fully overwritten by a single `.write_text(...)` call, which only
    ever had one writer; it now has two). Sections are delimited by HTML comment markers, so
    re-running either writer updates its own block in place and leaves the rest of the file,
    and its position in reading order, untouched.
    """
    start = f"<!-- SECTION:{marker}:START -->"
    end = f"<!-- SECTION:{marker}:END -->"
    block = f"{start}\n{content}\n{end}\n"

    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if start in existing and end in existing:
        pre, _, rest = existing.partition(start)
        _, _, post = rest.partition(end)
        new_content = pre + block + post
    else:
        separator = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        new_content = existing + separator + block

    path.write_text(new_content, encoding="utf-8")


def persist_case_mandate(session, case: CaseRecord) -> models.Mandate:
    """The ONLY sanctioned way to materialize a `CaseRecord`'s mandate as a real, persisted
    `models.Mandate` row -- added 2026-09-03 after `eval/run.py` was found constructing its
    own `models.Mandate(...)` inline and omitting `created_at`, silently defaulting it to the
    DB's `server_default=func.now()` (real wall-clock insert time) instead of the fixture's
    intended value. Since every fixture's transactions occur *before* that real insert time,
    `compute_velocity`'s `days_elapsed = max(1, (as_of - created_at).days)` floored to 1 for
    every single case, inflating velocity ratios across the board (see the postmortem in
    eval/calibration_log.md for the full account and confirmed scope).

    `eval/calibrate_baseline.py` never had this bug -- it never constructs a DB `Mandate` row
    at all, only consuming `CaseRecord.mandate` (the plain `_Mandate` dataclass above, whose
    `created_at` was always correctly parsed from the fixture). This function exists so
    `eval/run.py` (the one caller that DOES need a real persisted row, since
    `domain.pipeline.run_pipeline` writes FK-referencing rows against it) has no reason to
    ever construct `models.Mandate` by hand again -- one field list, one place, matching
    `CaseRecord.mandate` exactly, `created_at` included.
    """
    m = case.mandate
    mandate_row = models.Mandate(
        purpose=m.purpose,
        budget=m.budget,
        period_days=m.period_days,
        allowed_categories=m.allowed_categories,
        created_at=m.created_at,
    )
    session.add(mandate_row)
    session.flush()
    return mandate_row
