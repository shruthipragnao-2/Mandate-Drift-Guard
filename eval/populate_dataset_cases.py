"""Populate `dataset_cases` (Checkpoint M5 full-scale batch, step 6 -- DB population deferred
from the pilot). Reads the 100 fixtures written by eval/generate_full_dataset.py and writes one
row per case via the real SQLAlchemy models (backend/app/db/models.py) and a real Alembic-
migrated Postgres connection -- no hand-written SQL.

Dev/test split, stratified by drift_type at the pair level (never splitting a pair's two
members across dev/test) plus an independent split for the unpaired ambiguous cases -- both
via the same seeded shuffle (`eval.generate_full_dataset.seeded_split`, seed=42,
dev_fraction=0.35) used to decide which fixtures were written as dev vs test.

Each pair is inserted in two steps (legit first with paired_with_id=NULL, then drift with
paired_with_id=<legit's id>, then legit is updated to point back at drift) so the self-
referential FK is always satisfied at insert time.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_full_dataset import generate_all, seeded_split  # noqa: E402

from app.db.models import DatasetCase  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402


def main() -> None:
    pairs, ambiguous = generate_all()

    fast = [p for p in pairs if p[4].drift_type == "fast_spike"]
    slow = [p for p in pairs if p[4].drift_type == "slow_drift"]
    fast_dev, fast_test = seeded_split(fast)
    slow_dev, slow_test = seeded_split(slow)
    amb_dev, amb_test = seeded_split(ambiguous)

    dev_pair_keys = {(p[0], p[1]) for p in fast_dev + slow_dev}
    dev_amb_keys = {a[0] for a in amb_dev}

    session = SessionLocal()
    try:
        counts = {"dev": 0, "test": 0}
        for legit_path, drift_path, legit, drift, spec in pairs:
            split = "dev" if (legit_path, drift_path) in dev_pair_keys else "test"
            counts[split] += 2

            legit_id = uuid.uuid4()
            drift_id = uuid.uuid4()

            legit_row = DatasetCase(
                id=legit_id,
                split=split,
                category="legitimate",
                drift_type=spec.drift_type,
                paired_with_id=None,
                ground_truth_label=legit["ground_truth_label"],
                rationale=legit["rationale"],
                fixture_path=legit_path,
            )
            session.add(legit_row)
            session.flush()

            drift_row = DatasetCase(
                id=drift_id,
                split=split,
                category="drift",
                drift_type=spec.drift_type,
                paired_with_id=legit_id,
                ground_truth_label=drift["ground_truth_label"],
                rationale=drift["rationale"],
                fixture_path=drift_path,
            )
            session.add(drift_row)
            session.flush()

            legit_row.paired_with_id = drift_id
            session.flush()

        for relpath, case, spec in ambiguous:
            split = "dev" if relpath in dev_amb_keys else "test"
            counts[split] += 1
            row = DatasetCase(
                id=uuid.uuid4(),
                split=split,
                category="ambiguous",
                drift_type="n_a",
                paired_with_id=None,
                ground_truth_label=case["ground_truth_label"],
                rationale=case["rationale"],
                fixture_path=relpath,
            )
            session.add(row)

        session.commit()
        print(f"inserted {counts['dev'] + counts['test']} rows (dev={counts['dev']}, test={counts['test']})")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
