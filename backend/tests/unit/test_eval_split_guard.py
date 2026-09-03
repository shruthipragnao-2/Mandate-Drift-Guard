"""Safety-critical test (Checkpoint C11): eval/calibrate_baseline.py and eval/run.py must
never be able to load a dataset_cases row with split == "test" -- that boundary is what makes
C13's locked-test-set run meaningful ("touched exactly once, at the end", baseline §9). This
is not a nice-to-have: crossing this boundary early would silently contaminate the eventual
locked-test-set numbers.

Requires `pytest.ini`'s `pythonpath = . ../eval` so `dataset_loader`/`calibrate_baseline`/`run`
(bare names, matching the existing eval/ script convention of adding the eval/ directory
itself to sys.path rather than importing as a package) are importable here.
"""

from dataclasses import dataclass

import pytest

import calibrate_baseline
import dataset_loader
import run as eval_run
from dataset_loader import TestSplitAccessError, _assert_no_test_split


@dataclass
class _FakeDatasetCaseRow:
    id: str
    split: str


def test_assert_no_test_split_raises_on_a_test_row():
    """The core guard, tested directly and fast (no DB): one dev row and one test row in the
    same batch must still raise, not silently filter the bad one out."""
    rows = [_FakeDatasetCaseRow(id="dev-1", split="dev"), _FakeDatasetCaseRow(id="test-1", split="test")]

    with pytest.raises(TestSplitAccessError, match="test-1"):
        _assert_no_test_split(rows)


def test_assert_no_test_split_passes_on_all_dev_rows():
    rows = [_FakeDatasetCaseRow(id="dev-1", split="dev"), _FakeDatasetCaseRow(id="dev-2", split="dev")]

    _assert_no_test_split(rows)  # must not raise


def test_calibrate_baseline_uses_the_shared_guarded_loader():
    """Proves eval/calibrate_baseline.py does not reimplement its own dataset_cases query
    that could bypass the guard -- it imports the exact same function object."""
    assert calibrate_baseline.load_dev_cases is dataset_loader.load_dev_cases


def test_run_uses_the_shared_guarded_loader():
    """Same proof for eval/run.py."""
    assert eval_run.load_dev_cases is dataset_loader.load_dev_cases
