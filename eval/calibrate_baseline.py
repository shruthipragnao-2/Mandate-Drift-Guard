"""Calibrate the rules-only baseline's `threshold_T` (Checkpoint C11, eval-design.md §5).

Sweeps candidate threshold_T values against the DEV SET ONLY (via eval/dataset_loader.py's
hard split guard -- this script never sees split=="test" data) and picks the value maximizing
dev-set F1 (eval-design §7's exact formula), computed over the legitimate+drift cases only
(ambiguous cases are excluded from precision/recall scoring, per eval-design §7/§12).

Writes the full sweep table (not just the winner) to eval/calibration_log.md, so the choice is
reproducible and auditable, per eval-design §5's own "recorded explicitly... not eyeballed"
requirement.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dataset_loader import CaseRecord, load_dev_cases  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.domain.evidence_engine.category_shift import CategoryShiftResult, compute_category_shift  # noqa: E402
from app.domain.evidence_engine.velocity import VelocityResult, compute_velocity  # noqa: E402
from app.domain.rules_only_gate import decide  # noqa: E402

# Candidate grid: the category_shift band cutoffs themselves (0.05/0.20/0.45, Decision 10)
# plus intermediate points, so the sweep can land either exactly on a locked band boundary or
# between two of them.
CANDIDATE_THRESHOLDS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]


@dataclass(frozen=True)
class _ScoredCase:
    ground_truth_label: str  # "legitimate" | "drift"
    drift_type: str
    velocity_result: VelocityResult
    category_shift_result: CategoryShiftResult


def _score_case(case: CaseRecord) -> _ScoredCase:
    velocity_result = compute_velocity(case.mandate, case.transactions)
    category_shift_result = compute_category_shift(case.mandate, case.transactions)
    return _ScoredCase(
        ground_truth_label=case.ground_truth_label,
        drift_type=case.drift_type,
        velocity_result=velocity_result,
        category_shift_result=category_shift_result,
    )


def _confusion_matrix(scored_cases: list[_ScoredCase], threshold_t: float) -> dict:
    tp = fp = fn = tn = 0
    for case in scored_cases:
        result = decide(case.velocity_result, case.category_shift_result, threshold_t=threshold_t)
        flagged = result.decision == "hold"
        is_drift = case.ground_truth_label == "drift"

        if is_drift and flagged:
            tp += 1
        elif is_drift and not flagged:
            fn += 1
        elif not is_drift and flagged:
            fp += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall, "f1": f1}


def calibrate() -> tuple[float, dict, list[tuple[float, dict]], int, int]:
    """Reusable by eval/run.py to get the current best threshold_T without re-writing
    eval/calibration_log.md a second time (that file is calibrate_baseline.py's own exclusive
    responsibility, per this checkpoint's instructions) -- re-running the sweep itself is
    cheap (pure functions, no LLM calls, no side effects beyond the log write this function
    itself doesn't perform), so there is no staleness risk in calling this fresh each time.
    """
    session = SessionLocal()
    try:
        cases = load_dev_cases(session)
    finally:
        session.close()

    scored_cases = [_score_case(c) for c in cases if c.category in ("legitimate", "drift")]
    n_legit = sum(1 for c in scored_cases if c.ground_truth_label == "legitimate")
    n_drift = sum(1 for c in scored_cases if c.ground_truth_label == "drift")

    sweep_results = [(t, _confusion_matrix(scored_cases, t)) for t in CANDIDATE_THRESHOLDS]
    best_threshold, best_metrics = max(sweep_results, key=lambda row: row[1]["f1"])
    return best_threshold, best_metrics, sweep_results, n_legit, n_drift


def main() -> None:
    best_threshold, best_metrics, sweep_results, n_legit, n_drift = calibrate()

    print(f"dev-set legit={n_legit} drift={n_drift} (n={n_legit + n_drift})")
    print(f"{'threshold_T':>12}  {'TP':>3} {'FP':>3} {'FN':>3} {'TN':>3}  {'Precision':>9} {'Recall':>9} {'F1':>9}")
    for threshold_t, m in sweep_results:
        marker = "  <-- chosen" if threshold_t == best_threshold else ""
        print(
            f"{threshold_t:>12.2f}  {m['tp']:>3} {m['fp']:>3} {m['fn']:>3} {m['tn']:>3}  "
            f"{m['precision']:>9.4f} {m['recall']:>9.4f} {m['f1']:>9.4f}{marker}"
        )
    print(f"\nchosen threshold_T = {best_threshold} (dev-set F1 = {best_metrics['f1']:.4f})")

    _write_calibration_log(n_legit, n_drift, sweep_results, best_threshold, best_metrics)


def _write_calibration_log(n_legit, n_drift, sweep_results, best_threshold, best_metrics) -> None:
    lines = [
        "# Rules-Only Baseline Calibration Log — Checkpoint C11",
        "",
        "*eval/calibrate_baseline.py, run against the DEV SET ONLY (eval/dataset_loader.py's "
        "hard split guard) -- the locked test set was never touched. Per eval-design.md §5: "
        '"`threshold_T` is chosen by sweeping candidate values on the dev set and picking the '
        'value that maximizes dev-set F1 ... recorded explicitly so the choice is '
        'reproducible, not eyeballed."*',
        "",
        f"Dev-set legitimate/drift cases scored: {n_legit} legitimate, {n_drift} drift "
        f"(ambiguous cases excluded from F1, per eval-design §7/§12).",
        "",
        "Rule swept (eval-design §5, exact): "
        "`IF velocity == elevated AND category_shift_ratio >= threshold_T THEN HOLD ELSE ALLOW`.",
        "",
        "## Full sweep table",
        "",
        "| threshold_T | TP | FP | FN | TN | Precision | Recall | F1 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for threshold_t, m in sweep_results:
        chosen = " **(chosen)**" if threshold_t == best_threshold else ""
        lines.append(
            f"| {threshold_t:.2f}{chosen} | {m['tp']} | {m['fp']} | {m['fn']} | {m['tn']} | "
            f"{m['precision']:.4f} | {m['recall']:.4f} | {m['f1']:.4f} |"
        )
    lines += [
        "",
        f"## Chosen value: `threshold_T = {best_threshold}` (dev-set F1 = {best_metrics['f1']:.4f})",
        "",
        f"TP={best_metrics['tp']}, FP={best_metrics['fp']}, FN={best_metrics['fn']}, "
        f"TN={best_metrics['tn']}, Precision={best_metrics['precision']:.4f}, "
        f"Recall={best_metrics['recall']:.4f}.",
        "",
        "This value is read by `eval/run.py` when scoring the rules-only baseline -- not "
        "re-swept or hardcoded a second time.",
    ]
    (REPO_ROOT / "eval" / "calibration_log.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
