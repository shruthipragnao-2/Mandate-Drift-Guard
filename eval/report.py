"""Eval report (Checkpoint C11). Reads eval/results/dev_run_results.json (written by
eval/run.py) and computes the metric set from eval-design.md §7-18 -- read fresh from that
document, not paraphrased, since the exact formulas matter (see the docstring of each
computation below for the section it implements).

This is a DEV-SET report, not the locked-test-set report (that is Checkpoint C13, a separate,
one-time, non-repeatable run per docs/IMPLEMENTATION-PLAN.md §Q milestone M8). Numbers here
are for dev-set iteration/sanity-checking only and must never be quoted as the project's
reported result.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.config import GATE_POLICY_CONFIG  # noqa: E402

RESULTS_PATH = REPO_ROOT / "eval" / "results" / "dev_run_results.json"


def _flagged(gate_decision: str | None) -> bool:
    """eval-design §7: "System positive (flagged) = gate output is HOLD or BLOCK." Neither
    system in this dev-set run ever reaches BLOCK (that requires a timed-out, unresolved HOLD,
    not exercised by a synthetic batch run) -- HOLD is the only flagged value seen here."""
    return gate_decision == "hold"


def _confusion_matrix(cases: list[dict], decision_key: str) -> dict:
    """eval-design §7's exact confusion-matrix/formula set, restricted to legitimate+drift
    cases only (ambiguous is scored separately in §12, never forced into this binary matrix)."""
    tp = fp = fn = tn = 0
    for case in cases:
        if case["category"] not in ("legitimate", "drift"):
            continue
        decision = case[decision_key].get("gate_decision") if case[decision_key] else None
        flagged = _flagged(decision)
        is_drift = case["ground_truth_label"] == "drift"
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
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    fnr = fn / (fn + tp) if (fn + tp) > 0 else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall, "f1": f1, "fpr": fpr, "fnr": fnr}


def primary_metrics(cases: list[dict]) -> dict:
    """eval-design §7, "computed twice -- once for fast-spike, once for slow-drift ... never
    blended", for both systems side by side."""
    out = {}
    for drift_type in ("fast_spike", "slow_drift"):
        subset = [c for c in cases if c["drift_type"] == drift_type]
        out[drift_type] = {
            "rules_only": _confusion_matrix(subset, "rules_only"),
            "hybrid": _confusion_matrix(subset, "hybrid"),
        }
    return out


def abstention_metrics(cases: list[dict]) -> dict:
    """eval-design §12. Scored on the hybrid system only (the rules-only baseline has no
    abstention concept of its own -- it only ever outputs allow/hold)."""
    ambiguous = [c for c in cases if c["category"] == "ambiguous"]
    legitimate = [c for c in cases if c["category"] == "legitimate"]

    n_amb = len(ambiguous)
    correct_abstention = sum(1 for c in ambiguous if (c["hybrid"] or {}).get("gate_decision") == "hold")
    overconfident = sum(1 for c in ambiguous if (c["hybrid"] or {}).get("gate_decision") in ("allow",))

    n_legit = len(legitimate)
    unnecessary_hold = sum(1 for c in legitimate if (c["hybrid"] or {}).get("gate_decision") == "hold")

    return {
        "n_ambiguous": n_amb,
        "correct_abstention_rate": correct_abstention / n_amb if n_amb else None,
        "overconfidence_on_ambiguous_rate": overconfident / n_amb if n_amb else None,
        "n_legitimate": n_legit,
        "unnecessary_hold_rate": unnecessary_hold / n_legit if n_legit else None,
    }


def gate_rule_violation_count(cases: list[dict]) -> int:
    """eval-design §14: count(risk_level=="high" AND confidence<calibration_threshold AND
    gate_output=="ALLOW"). `calibration_threshold` = GatePolicyConfig.confidence_floor (the
    only calibration-related confidence threshold the gate defines, Decision 15). Should be 0
    by construction (C10's policy_gate.py proves this at the unit level via an exhaustive
    sweep) -- measured here over the actual dev-set run, not assumed."""
    floor = GATE_POLICY_CONFIG.confidence_floor
    count = 0
    for case in cases:
        hybrid = case["hybrid"] or {}
        if (
            hybrid.get("risk_level") == "high"
            and hybrid.get("confidence") is not None
            and hybrid["confidence"] < floor
            and hybrid.get("gate_decision") == "allow"
        ):
            count += 1
    return count


def audit_completeness_rate(cases: list[dict]) -> dict:
    """eval-design §15: fraction of cases with a fully populated audit record. "Fully
    populated" here = an audit_events row exists, and -- for cases whose threshold crossed --
    an evidence_packets row and a gate_decisions row also exist (semantic_assessments is
    intentionally excluded from this check: Decision 5 means a failed LLM call legitimately
    has NO semantic_assessments row, so its absence there is not incompleteness)."""
    total = 0
    complete = 0
    for case in cases:
        hybrid = case["hybrid"]
        if not hybrid or case.get("pipeline_error"):
            total += 1
            continue
        total += 1
        ok = bool(hybrid.get("audit_event_present"))
        if hybrid.get("threshold_crossed"):
            ok = ok and bool(hybrid.get("gate_decision_row_present")) and bool(hybrid.get("evidence_packet_present"))
        if ok:
            complete += 1
    return {"total": total, "complete": complete, "rate": complete / total if total else None}


def drift_cases_caught_only_by_hybrid(cases: list[dict]) -> int:
    """eval-design §9: count(GT=drift AND baseline=ALLOW AND hybrid in {HOLD, BLOCK}) -- the
    single number proving the AI layer is necessary, not additive."""
    count = 0
    for case in cases:
        if case["ground_truth_label"] != "drift":
            continue
        rules_only_decision = (case["rules_only"] or {}).get("gate_decision")
        hybrid_decision = (case["hybrid"] or {}).get("gate_decision")
        if rules_only_decision == "allow" and hybrid_decision == "hold":
            count += 1
    return count


def reliability_metrics(cases: list[dict]) -> dict:
    """eval-design §16, the subset computable without a retry counter (not tracked by
    semantic_risk_client's return type -- Decision 14 makes retries transport-only and
    invisible to the outcome status): schema-validation pass rate and pipeline error rate."""
    triggered = [c for c in cases if not c.get("pipeline_error") and (c["hybrid"] or {}).get("threshold_crossed")]
    llm_calls = len(triggered)
    successes = sum(1 for c in triggered if c["hybrid"]["llm_status"] == "success")
    errors = sum(1 for c in cases if c.get("pipeline_error"))
    return {
        "llm_calls": llm_calls,
        "schema_validation_pass_rate": successes / llm_calls if llm_calls else None,
        "pipeline_error_rate": errors / len(cases) if cases else None,
        "pipeline_error_count": errors,
    }


def gate_decision_distribution(cases: list[dict]) -> dict:
    """eval-design §8's sanity-check distribution, hybrid system only."""
    total = len(cases)
    counts = {"allow": 0, "hold": 0, "none": 0}
    for case in cases:
        hybrid = case["hybrid"] or {}
        decision = hybrid.get("gate_decision")
        counts["none" if decision is None else decision] += 1
    return {k: (v / total if total else None) for k, v in counts.items()}


def main() -> None:
    data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]
    threshold_t = data["threshold_t"]

    print(f"=== Dev-set report ({len(cases)} cases, rules-only threshold_T={threshold_t}) ===\n")

    print("--- §7 Primary metrics (precision/recall/F1/FPR/FNR), by drift_type ---")
    primary = primary_metrics(cases)
    for drift_type, systems in primary.items():
        print(f"\n{drift_type}:")
        for system_name, m in systems.items():
            print(
                f"  {system_name:>11}: TP={m['tp']} FP={m['fp']} FN={m['fn']} TN={m['tn']}  "
                f"P={m['precision']:.4f} R={m['recall']:.4f} F1={m['f1']:.4f} "
                f"FPR={m['fpr']:.4f} FNR={m['fnr']:.4f}"
            )

    print("\n--- §9 Drift_cases_caught_only_by_hybrid ---")
    caught_only_by_hybrid = drift_cases_caught_only_by_hybrid(cases)
    print(f"  {caught_only_by_hybrid}")

    print("\n--- §12 Abstention metrics ---")
    abstention = abstention_metrics(cases)
    for k, v in abstention.items():
        print(f"  {k}: {v}")

    print("\n--- §14 Gate-rule-violation count (should be 0) ---")
    violations = gate_rule_violation_count(cases)
    print(f"  {violations}")

    print("\n--- §15 Audit completeness ---")
    audit = audit_completeness_rate(cases)
    print(f"  {audit}")

    print("\n--- §16 Reliability ---")
    reliability = reliability_metrics(cases)
    print(f"  {reliability}")

    print("\n--- §8 Gate-decision distribution (hybrid) ---")
    distribution = gate_decision_distribution(cases)
    print(f"  {distribution}")

    report = {
        "n_cases": len(cases),
        "threshold_t": threshold_t,
        "primary_metrics": primary,
        "drift_cases_caught_only_by_hybrid": caught_only_by_hybrid,
        "abstention_metrics": abstention,
        "gate_rule_violation_count": violations,
        "audit_completeness": audit,
        "reliability_metrics": reliability,
        "gate_decision_distribution": distribution,
    }
    out_path = REPO_ROOT / "eval" / "results" / "dev_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote full report to {out_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
