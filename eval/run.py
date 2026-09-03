"""Eval harness runner (Checkpoint C11). Runs BOTH the hybrid pipeline
(`domain.pipeline.run_pipeline`, in-process -- not via HTTP, per architecture's own
reproducibility requirement) and the rules-only baseline (`domain.rules_only_gate`) over the
DEV SET ONLY, via eval/dataset_loader.py's hard split guard -- the locked test set is never
touched by this checkpoint (that is C13's dedicated one-time run).

For each case: the fixture's transaction stream is split into "historical" (all but the last,
persisted as already-`allowed` rows) and one "incoming" transaction (the last, chronologically
-- mirroring packet_builder.py's own historical/current split), which is what actually gets
evaluated. A fresh mandate is created per case. Real Anthropic API calls are made for every
case whose deterministic signals cross the threshold (no LLM mocking here -- this is a real
measurement run, not a unit test).

Writes eval/results/dev_run_results.json for eval/report.py to consume -- the anti-cherry-
picking discipline (eval-design.md, "Deliberately designed failure cases" section): every
number in the report traces back to this one batch file, not a hand-picked example.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from calibrate_baseline import calibrate  # noqa: E402
from dataset_loader import CaseRecord, load_dev_cases, persist_case_mandate  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import models  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.domain.evidence_engine.category_shift import compute_category_shift  # noqa: E402
from app.domain.evidence_engine.velocity import compute_velocity  # noqa: E402
from app.domain.pipeline import IncomingTransaction, run_pipeline  # noqa: E402
from app.domain.rules_only_gate import decide as rules_only_decide  # noqa: E402

RESULTS_DIR = REPO_ROOT / "eval" / "results"


def _run_hybrid(session, case: CaseRecord) -> dict:
    # Centralized in eval/dataset_loader.py (2026-09-03 fix) -- this used to construct
    # models.Mandate(...) inline here and silently dropped created_at, which floored
    # every case's velocity days_elapsed to 1 (see eval/calibration_log.md's postmortem).
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


def main() -> None:
    threshold_t, _, _, _, _ = calibrate()
    print(f"using calibrated threshold_T = {threshold_t} (from eval/calibrate_baseline.py)")

    session = SessionLocal()
    try:
        cases = load_dev_cases(session)
        print(f"loaded {len(cases)} dev-set cases")

        records = []
        for i, case in enumerate(cases, start=1):
            print(f"[{i}/{len(cases)}] {case.fixture_path} ({case.category}/{case.drift_type}) ...", end=" ", flush=True)
            error = None
            try:
                hybrid = _run_hybrid(session, case)
            except Exception as exc:  # noqa: BLE001 -- eval-design §16's pipeline error rate
                # must count every unhandled exception, not just ones from a narrower type;
                # a batch run must not halt on one bad case (eval-design's own anti-cherry-
                # picking requirement needs the full batch to complete in one pass).
                session.rollback()
                hybrid = {"error": str(exc)}
                error = str(exc)
                print(f"PIPELINE ERROR: {exc}")

            rules_only = _run_rules_only(case, threshold_t)
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

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_DIR / "dev_run_results.json"
    results_path.write_text(
        json.dumps({"threshold_t": threshold_t, "cases": records}, indent=2), encoding="utf-8"
    )
    print(f"\nwrote {len(records)} case results to {results_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
