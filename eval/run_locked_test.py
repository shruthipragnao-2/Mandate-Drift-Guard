"""Locked test-set run (Checkpoint C13 / milestone M8, eval/report.py's own docstring: "a
separate, one-time, non-repeatable run"). Runs BOTH the hybrid pipeline
(`domain.pipeline.run_pipeline`, in-process, matching eval/run.py's own reproducibility
argument) and the rules-only baseline (`domain.rules_only_gate`) over the LOCKED TEST SET --
and only the locked test set -- via `dataset_loader.load_test_cases(session, confirm=True)`,
the one function in this project permitted to read `dataset_cases` rows with split == "test".

This is a deliberately separate, self-contained script, not a `--split` flag bolted onto
eval/run.py. eval/run.py is not imported here and not modified by this file -- a later change
to its dev-set-only logic must never be able to silently alter what this script does. The
per-case pipeline/rules-only logic below is therefore a intentional duplicate of
eval/run.py's `_run_hybrid`/`_run_rules_only`, not a shared import.

What IS reused, deliberately: eval/report.py's metric functions (primary_metrics,
drift_cases_caught_only_by_hybrid, etc.), because those implement eval-design's "exact
formulas locked ... not reinvented during implementation" -- reusing them here (rather than
recomputing by hand) is required by this project's own "generation is not verification" /
no-hand-calculation discipline, applied to metrics rather than data.

`threshold_T` is a hardcoded literal below, NOT obtained by calling `calibrate_baseline.calibrate()`
fresh -- recalibrating against test data would be the exact dev/test contamination this
project has structurally avoided throughout (see eval/calibration_log.md's "Prompt Calibration
Verdict" section for the same discipline applied to prompt iteration).

Writes eval/results/LOCKED_test_report.json -- a new, distinct filename from
eval/results/dev_report.json so the two are never confused -- and appends a permanent, dated
section to eval/calibration_log.md via the existing `upsert_log_section` pattern.

Guard against accidental re-invocation: since this run is supposed to be terminal and
non-repeatable, main() refuses to proceed if eval/results/LOCKED_test_report.json already
exists, rather than silently overwriting a prior locked run under the same filename.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dataset_loader import CaseRecord, load_test_cases, persist_case_mandate, upsert_log_section  # noqa: E402
from report import (  # noqa: E402
    abstention_metrics,
    audit_completeness_rate,
    drift_cases_caught_only_by_hybrid,
    gate_decision_distribution,
    gate_rule_violation_count,
    primary_metrics,
    reliability_metrics,
    single_signal_legitimate_decision15_clearance,
)

from app.config import GATE_POLICY_CONFIG, settings  # noqa: E402
from app.db import models  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.domain.evidence_engine.category_shift import compute_category_shift  # noqa: E402
from app.domain.evidence_engine.velocity import compute_velocity  # noqa: E402
from app.domain.pipeline import IncomingTransaction, run_pipeline  # noqa: E402
from app.domain.rules_only_gate import decide as rules_only_decide  # noqa: E402
from app.domain.semantic_risk_client import PROMPT_VERSION  # noqa: E402

RESULTS_DIR = REPO_ROOT / "eval" / "results"
RESULTS_PATH = RESULTS_DIR / "LOCKED_test_report.json"
CALIBRATION_LOG_PATH = REPO_ROOT / "eval" / "calibration_log.md"

# threshold_T = 0.05, hardcoded deliberately (NOT calibrate_baseline.calibrate() called fresh
# here -- see module docstring). Sourced from calibrate() run against the real, repopulated
# dev set on 2026-09-04: n_legit=14, n_drift=14, best_metrics={tp:4, fp:4, fn:10, tn:10,
# precision:0.5, recall:0.2857, f1:0.3636}, the unique best across the full 0.05-0.50 sweep
# grid (F1 0.3636 vs 0.2222 everywhere else) -- matching the original pre-wipe calibration
# recorded in eval/calibration_log.md's "Chosen value" section exactly.
THRESHOLD_T = 0.05


def _run_hybrid(session, case: CaseRecord) -> dict:
    mandate_row = persist_case_mandate(session, case)

    *historical_raw, incoming_raw = case.transactions
    historical_rows = []
    for t in historical_raw:
        row = models.Transaction(
            mandate_id=mandate_row.id,
            merchant=t.merchant,
            category=t.category,
            amount=t.amount,
            occurred_at=t.occurred_at,
            idempotency_key=str(uuid.uuid4()),
            state="allowed",
        )
        session.add(row)
        historical_rows.append(row)
    session.flush()

    incoming = IncomingTransaction(
        merchant=incoming_raw.merchant,
        category=incoming_raw.category,
        amount=incoming_raw.amount,
        occurred_at=incoming_raw.occurred_at,
        idempotency_key=str(uuid.uuid4()),
    )

    result = run_pipeline(session, mandate_row, historical_rows, incoming, app_settings=settings)

    semantic_assessment = None
    if result.threshold_crossed:
        semantic_assessment = (
            session.query(models.SemanticAssessment)
            .join(models.EvidencePacket)
            .filter(models.EvidencePacket.transaction_id == result.transaction_id)
            .first()
        )
    audit_event_present = (
        session.query(models.AuditEvent).filter(models.AuditEvent.transaction_id == result.transaction_id).count() > 0
    )
    gate_decision_row_present = (
        session.query(models.GateDecision).filter(models.GateDecision.transaction_id == result.transaction_id).count()
        > 0
    )
    evidence_packet_present = (
        session.query(models.EvidencePacket).filter(models.EvidencePacket.transaction_id == result.transaction_id).count()
        > 0
    )

    return {
        "transaction_id": str(result.transaction_id),
        "threshold_crossed": result.threshold_crossed,
        "triggering_signals": list(result.triggering_signals),
        "gate_decision": result.gate_decision,
        "case_id_db": str(result.case_id) if result.case_id else None,
        "llm_status": result.llm_status,
        # Decision 20 (baseline §24): the exception type when run_pipeline's fail-closed
        # backstop caught an otherwise-unhandled exception, else None. Recorded because such
        # an exception no longer escapes run_pipeline for the `except` below to catch, and
        # eval-design §16's pipeline-error rate would silently undercount without it --
        # eval/report.py's `is_pipeline_error` reads this field.
        "fail_closed_reason": result.fail_closed_reason,
        "risk_level": semantic_assessment.risk_level if semantic_assessment else None,
        "mandate_alignment": semantic_assessment.mandate_alignment if semantic_assessment else None,
        "confidence": float(semantic_assessment.confidence) if semantic_assessment else None,
        "latency_ms": semantic_assessment.latency_ms if semantic_assessment else None,
        "audit_event_present": audit_event_present,
        "gate_decision_row_present": gate_decision_row_present if result.threshold_crossed else None,
        "evidence_packet_present": evidence_packet_present if result.threshold_crossed else None,
    }


def _run_rules_only(case: CaseRecord, threshold_t: float) -> dict:
    velocity_result = compute_velocity(case.mandate, case.transactions)
    category_shift_result = compute_category_shift(case.mandate, case.transactions)
    result = rules_only_decide(velocity_result, category_shift_result, threshold_t=threshold_t)
    return {"gate_decision": result.decision, "rule_applied": result.rule_applied}


def _print_confirmation_banner(n_cases: int) -> None:
    banner = f"""
{'=' * 78}
  LOCKED TEST-SET RUN -- CHECKPOINT C13 / MILESTONE M8
{'=' * 78}
  This is the ONE-TIME, IRREVERSIBLE evaluation run against the locked test
  split. Per docs/IMPLEMENTATION-BASELINE.md's "Dev set vs locked test set"
  policy, this data is touched EXACTLY ONCE, at the end -- there is no re-run.
  Whatever this script produces is the number that gets reported.

  Test-split cases loaded:  {n_cases}
  threshold_T:               {THRESHOLD_T} (hardcoded -- NOT recalibrated against this data)
  prompt_version:            {PROMPT_VERSION}

  Proceeding will make real Anthropic API calls (one per case whose
  deterministic signals cross the threshold) and permanently write:
    - {RESULTS_PATH.relative_to(REPO_ROOT)}
    - a new dated section in {CALIBRATION_LOG_PATH.relative_to(REPO_ROOT)}

  THIS CANNOT BE UNDONE OR CLEANLY RE-RUN. Interrupt now (Ctrl-C) if this was
  not an explicit, reviewed go-ahead.
{'=' * 78}
"""
    print(banner)


def _write_locked_test_summary_to_log(report: dict, primary: dict) -> None:
    """Permanent, dated section of eval/calibration_log.md recording the final locked-test-set
    result -- appended via `upsert_log_section` under a marker distinct from
    "DEV_RUN_SUMMARY", so this section and the dev-set section coexist rather than colliding.
    Mirrors eval/report.py's `_write_dev_run_summary_to_log` heading structure for
    readability, but states plainly that these numbers are final.
    """
    lines = [
        f"# LOCKED Test-Set Run — Checkpoint C13 / Milestone M8 ({report['run_date']})",
        "",
        f"**This is the final, one-time locked-test-set run. These are the numbers that get "
        f"reported.** No further threshold or prompt change may follow this run without "
        f"invalidating it -- per docs/IMPLEMENTATION-BASELINE.md's \"touched exactly once, at "
        f"the end\" policy, this section is not expected to ever be regenerated. If it ever is, "
        f"that is itself a finding to report, not a routine rerun.",
        "",
        f"*eval/run_locked_test.py, run against the LOCKED TEST SET ONLY "
        f"(eval/dataset_loader.py's `load_test_cases(confirm=True)`, the sole sanctioned reader "
        f"of split='test' rows). {report['n_cases']} cases, prompt_version=\"{report['prompt_version']}\", "
        f"rules-only threshold_T={report['threshold_t']} (hardcoded, not recalibrated against "
        f"this data).*",
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
    upsert_log_section(CALIBRATION_LOG_PATH, "LOCKED_TEST_SET_RUN", "\n".join(lines))


def main() -> None:
    if RESULTS_PATH.exists():
        print(
            f"REFUSING TO PROCEED: {RESULTS_PATH.relative_to(REPO_ROOT)} already exists.\n"
            "This run is designed to be terminal and non-repeatable -- it will not overwrite "
            "an existing locked-test-set report. If this file is stale/wrong, that is a human "
            "decision to make explicitly (move it aside, or decide a re-run is warranted), not "
            "something this script should do silently."
        )
        sys.exit(1)

    session = SessionLocal()
    try:
        cases = load_test_cases(session, confirm=True)
        _print_confirmation_banner(len(cases))

        records = []
        for i, case in enumerate(cases, start=1):
            print(f"[{i}/{len(cases)}] {case.fixture_path} ({case.category}/{case.drift_type}) ...", end=" ", flush=True)
            error = None
            try:
                hybrid = _run_hybrid(session, case)
            except Exception as exc:  # noqa: BLE001 -- eval-design §16's pipeline error rate
                # must count every unhandled exception, not just ones from a narrower type;
                # a locked batch run must not halt partway through -- the anti-cherry-picking
                # requirement needs the full batch to complete in one pass.
                session.rollback()
                hybrid = {"error": str(exc)}
                error = str(exc)
                print(f"PIPELINE ERROR: {exc}")

            rules_only = _run_rules_only(case, THRESHOLD_T)
            if error is None:
                print(f"hybrid={hybrid['gate_decision']} rules_only={rules_only['gate_decision']}")

            records.append(
                {
                    "case_id": case.id,
                    "fixture_path": case.fixture_path,
                    "category": case.category,
                    "drift_type": case.drift_type,
                    "ground_truth_label": case.ground_truth_label,
                    "hybrid": hybrid,
                    "rules_only": rules_only,
                    "pipeline_error": error,
                }
            )
    finally:
        session.close()

    primary = primary_metrics(records)
    report = {
        "run_kind": "LOCKED_TEST_SET -- Checkpoint C13 / Milestone M8 -- one-time, non-repeatable",
        "run_date": date.today().isoformat(),
        "n_cases": len(records),
        "threshold_t": THRESHOLD_T,
        "prompt_version": PROMPT_VERSION,
        "primary_metrics": primary,
        "drift_cases_caught_only_by_hybrid": drift_cases_caught_only_by_hybrid(records),
        "abstention_metrics": abstention_metrics(records),
        "gate_rule_violation_count": gate_rule_violation_count(records),
        "audit_completeness": audit_completeness_rate(records),
        "reliability_metrics": reliability_metrics(records),
        "gate_decision_distribution": gate_decision_distribution(records),
        "decision15_single_signal_legitimate_clearance": single_signal_legitimate_decision15_clearance(
            records, GATE_POLICY_CONFIG.confidence_floor
        ),
        "cases": records,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {len(records)} case results to {RESULTS_PATH.relative_to(REPO_ROOT)}")

    _write_locked_test_summary_to_log(report, primary)
    print(f"appended locked-test-set summary to {CALIBRATION_LOG_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
