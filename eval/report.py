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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dataset_loader import upsert_log_section  # noqa: E402

from app.config import GATE_POLICY_CONFIG  # noqa: E402
from app.domain.semantic_risk_client import PROMPT_VERSION  # noqa: E402

RESULTS_PATH = REPO_ROOT / "eval" / "results" / "dev_run_results.json"
CALIBRATION_LOG_PATH = REPO_ROOT / "eval" / "calibration_log.md"


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
        # Same predicate as the error rate above, so one failed case cannot be an error by
        # one metric and a complete audit record by the other. Note the deliberate asymmetry
        # this preserves: a backstop case DOES now have an audit event (that is precisely what
        # Decision 20 restored), but it is still counted as a pipeline error -- "we recorded
        # why it failed" is not the same claim as "it did not fail".
        if not hybrid or is_pipeline_error(case):
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


def is_pipeline_error(case: dict) -> bool:
    """eval-design §16's pipeline-error predicate. Two sources, because Decision 20
    (docs/IMPLEMENTATION-BASELINE.md §24) changed how a pipeline failure reaches the harness --
    this is a metric-definition consistency fix, not a change to what counts as an error.

    1. `pipeline_error` -- an exception the runner caught around `run_pipeline`. This was the
       ONLY source before Decision 20.
    2. `hybrid.fail_closed_reason` -- set when `run_pipeline`'s fail-closed backstop caught an
       otherwise-unhandled exception and routed it to HOLD (RT-C1-008's fix). Such an exception
       no longer escapes `run_pipeline`, so source 1 can no longer see it. Counting only source
       1 from here on would report zero errors for runs in which the pipeline genuinely threw
       -- an undercount that gets quieter the better the backstop works.

    Both are the same underlying event: the pipeline hit something it did not anticipate. What
    changed is that the system now fails closed and keeps an audit record instead of 500ing
    with nothing persisted; the metric must keep counting it either way.

    C13's locked test-set numbers are unaffected by this, and not merely "left alone": that run
    recorded `pipeline_error_count: 0` with `llm_status_counts: {success: 60}` over 60 LLM
    calls, i.e. zero exceptions were raised at all. Zero exceptions then means zero backstop
    catches now -- both counts are a true zero over the same events, not a metric that quietly
    changed meaning underneath a number that happened to stay 0. The locked run is NOT re-run
    and its recorded results files are not rewritten; this predicate applies to future runs.
    """
    if case.get("pipeline_error"):
        return True
    return bool((case.get("hybrid") or {}).get("fail_closed_reason"))


def reliability_metrics(cases: list[dict]) -> dict:
    """eval-design §16, the subset computable without a retry counter (not tracked by
    semantic_risk_client's return type -- Decision 14 makes retries transport-only and
    invisible to the outcome status): schema-validation pass rate and pipeline error rate.
    Also breaks out `llm_status` counts (success/timeout/malformed/transport_error) and a
    timeout rate specifically -- a single genuinely slow run can otherwise hide inside a
    pass-rate average."""
    triggered = [c for c in cases if not is_pipeline_error(c) and (c["hybrid"] or {}).get("threshold_crossed")]
    llm_calls = len(triggered)
    status_counts: dict[str, int] = {}
    for c in triggered:
        status = c["hybrid"]["llm_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    successes = status_counts.get("success", 0)
    timeouts = status_counts.get("timeout", 0)
    errors = sum(1 for c in cases if is_pipeline_error(c))
    return {
        "llm_calls": llm_calls,
        "llm_status_counts": status_counts,
        "schema_validation_pass_rate": successes / llm_calls if llm_calls else None,
        "timeout_rate": timeouts / llm_calls if llm_calls else None,
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


def single_signal_legitimate_decision15_clearance(cases: list[dict], confidence_floor: float) -> dict:
    """Not an eval-design §7-18 formula -- a diagnostic specific to this project's Decision 15
    bounded-downgrade path (docs/IMPLEMENTATION-BASELINE.md §20). Filters to
    triggering_signal_count==1 AND ground_truth_label=="legitimate" (the only subset where the
    downgrade is even structurally reachable AND where the ground truth says it SHOULD
    ideally fire), and reports how many clear all three of Decision 15's conditions:
    risk_level=="low", confidence>=confidence_floor, mandate_alignment!="low"."""
    subset = [
        c for c in cases
        if len((c["hybrid"] or {}).get("triggering_signals") or []) == 1
        and c["ground_truth_label"] == "legitimate"
    ]
    cleared = []
    for c in subset:
        h = c["hybrid"] or {}
        if (
            h.get("risk_level") == "low"
            and h.get("confidence") is not None
            and h["confidence"] >= confidence_floor
            and h.get("mandate_alignment") != "low"
        ):
            cleared.append(c["case_id"])
    return {"n_subset": len(subset), "n_cleared": len(cleared), "cleared_case_ids": cleared}


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

    print("\n--- Decision 15 clearance: single-signal/legitimate subset ---")
    decision15 = single_signal_legitimate_decision15_clearance(cases, GATE_POLICY_CONFIG.confidence_floor)
    print(f"  {decision15}")

    report = {
        "n_cases": len(cases),
        "threshold_t": threshold_t,
        "prompt_version": PROMPT_VERSION,
        "primary_metrics": primary,
        "drift_cases_caught_only_by_hybrid": caught_only_by_hybrid,
        "abstention_metrics": abstention,
        "gate_rule_violation_count": violations,
        "audit_completeness": audit,
        "reliability_metrics": reliability,
        "gate_decision_distribution": distribution,
        "decision15_single_signal_legitimate_clearance": decision15,
    }
    out_path = REPO_ROOT / "eval" / "results" / "dev_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote full report to {out_path.relative_to(REPO_ROOT)}")

    _write_dev_run_summary_to_log(report, primary)


def _write_dev_run_summary_to_log(report: dict, primary: dict) -> None:
    """Permanent, always-current dev-set-run summary section of eval/calibration_log.md --
    baked into this script's own write path (via upsert_log_section, not a one-off manual
    edit) so every future `eval/report.py` run keeps this section up to date without
    disturbing eval/calibrate_baseline.py's sweep-table section elsewhere in the same file."""
    lines = [
        f"# Dev-Set Run Summary — prompt_version=\"{report['prompt_version']}\"",
        "",
        f"*eval/run.py + eval/report.py, run against the DEV SET ONLY "
        f"(eval/dataset_loader.py's hard split guard) -- the locked test set was never "
        f"touched. {report['n_cases']} cases, rules-only threshold_T={report['threshold_t']}.*",
        "",
        "## §7 Primary metrics (precision/recall/F1/FPR), by drift_type",
        "",
    ]
    for drift_type, systems in primary.items():
        lines.append(f"**{drift_type}**")
        for system_name, m in systems.items():
            lines.append(
                f"- {system_name}: TP={m['tp']} FP={m['fp']} FN={m['fn']} TN={m['tn']} "
                f"P={m['precision']:.4f} R={m['recall']:.4f} F1={m['f1']:.4f} FPR={m['fpr']:.4f}"
            )
        lines.append("")
    lines += [
        f"## §8 Gate-decision distribution (hybrid): {report['gate_decision_distribution']}",
        "",
        f"## §9 Drift_cases_caught_only_by_hybrid: {report['drift_cases_caught_only_by_hybrid']}",
        "",
        f"## §12 Abstention metrics: {report['abstention_metrics']}",
        "",
        f"## §14 Gate-rule-violation count (measured, target 0): {report['gate_rule_violation_count']}",
        "",
        f"## §15 Audit completeness: {report['audit_completeness']}",
        "",
        f"## §16 Reliability: {report['reliability_metrics']}",
        "",
        f"## Decision 15 clearance, single-signal/legitimate subset: "
        f"{report['decision15_single_signal_legitimate_clearance']}",
    ]
    upsert_log_section(CALIBRATION_LOG_PATH, "DEV_RUN_SUMMARY", "\n".join(lines))


if __name__ == "__main__":
    main()
