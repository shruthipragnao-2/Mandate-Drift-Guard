"""Basic model-construction / round-trip tests (Checkpoint C6 / milestone M1).

Not pipeline tests -- no evidence-engine logic, no gate logic, no LLM client exists yet
(those are later milestones). These only confirm the ORM models as written actually persist
and read back correctly against the real schema, end to end, for both the nominal-ALLOW
shape and the full HOLD-to-resolution shape.
"""

import uuid
from datetime import datetime, timezone

from app.db import models


def test_nominal_allow_transaction_round_trip(db_session, make_mandate):
    """A transaction that never crosses a threshold: allowed at insert time (Decision 4), no
    evidence packet, no case -- just one audit event recording the nominal pass."""
    mandate = make_mandate()
    txn = models.Transaction(
        mandate_id=mandate.id,
        merchant="Corner Store",
        category="groceries",
        amount=350,
        occurred_at=datetime.now(timezone.utc),
        idempotency_key=str(uuid.uuid4()),
        state="allowed",
    )
    db_session.add(txn)
    db_session.flush()

    event = models.AuditEvent(
        case_id=None,
        mandate_id=mandate.id,
        transaction_id=txn.id,
        event_type="evaluated_threshold_not_crossed",
        payload={"decision": "allow"},
    )
    db_session.add(event)
    db_session.flush()
    db_session.expire_all()

    reloaded = db_session.get(models.Transaction, txn.id)
    assert reloaded.state == "allowed"
    assert reloaded.mandate_id == mandate.id
    assert len(reloaded.audit_events) == 1
    assert reloaded.audit_events[0].case_id is None
    assert reloaded.case is None


def test_full_hold_to_resolution_round_trip(db_session, make_mandate):
    """mandate -> transaction(held) -> evidence_packet -> semantic_assessment ->
    gate_decision(hold) -> case(hold) -> [Ops resolves] -> case(resolved_allow), with an
    audit_event at every stage -- exercising every table and every documented FK at once."""
    mandate = make_mandate(
        purpose="weekly household groceries",
        budget=8000,
        period_days=7,
        allowed_categories=["groceries", "household essentials"],
    )

    txn = models.Transaction(
        mandate_id=mandate.id,
        merchant="Electronics Superstore",
        category="electronics",
        amount=7200,
        occurred_at=datetime.now(timezone.utc),
        idempotency_key=str(uuid.uuid4()),
        state="held",
    )
    db_session.add(txn)
    db_session.flush()

    packet = models.EvidencePacket(
        mandate_id=mandate.id,
        transaction_id=txn.id,
        signals={"budget_utilization": 0.91, "spend_velocity": "elevated", "category_shift": "significant"},
        trajectory={"historical_distribution": "groceries: 95%", "current_distribution": "electronics: 90%"},
    )
    db_session.add(packet)
    db_session.flush()

    assessment = models.SemanticAssessment(
        evidence_packet_id=packet.id,
        mandate_alignment="low",
        risk_level="high",
        confidence=0.91,
        evidence=[
            "spend has shifted away from allowed categories",
            "pattern persists across multiple transactions, not one outlier",
        ],
        raw_response={
            "mandate_alignment": "low",
            "risk_level": "high",
            "confidence": 0.91,
            "evidence": ["..."],
        },
        model_version="claude-test-pin",
        prompt_version="v1",
        latency_ms=612,
    )
    db_session.add(assessment)
    db_session.flush()

    gate_decision = models.GateDecision(
        semantic_assessment_id=assessment.id,
        transaction_id=txn.id,
        decision="hold",
        rule_version="v1",
        rule_applied="medium/high risk -> HOLD",
    )
    db_session.add(gate_decision)
    db_session.flush()

    case = models.Case(
        mandate_id=mandate.id,
        transaction_id=txn.id,
        gate_decision_id=gate_decision.id,
        state="hold",
    )
    db_session.add(case)
    db_session.flush()

    opened_event = models.AuditEvent(
        case_id=case.id,
        mandate_id=mandate.id,
        transaction_id=txn.id,
        event_type="case_opened",
        payload={"gate_decision": "hold", "rule_version": "v1"},
    )
    db_session.add(opened_event)
    db_session.flush()

    # Ops analyst resolves the HOLD -> ALLOW.
    case.state = "resolved_allow"
    case.resolved_at = datetime.now(timezone.utc)
    case.resolved_by = "ops:jane"
    case.resolution_reason = "confirmed legitimate one-time purchase with consumer"
    txn.state = "allowed"
    db_session.flush()

    resolved_event = models.AuditEvent(
        case_id=case.id,
        mandate_id=mandate.id,
        transaction_id=txn.id,
        event_type="case_resolved",
        payload={"resolution": "confirm", "resolved_by": "ops:jane"},
    )
    db_session.add(resolved_event)
    db_session.flush()
    db_session.expire_all()

    reloaded_case = db_session.get(models.Case, case.id)
    assert reloaded_case.state == "resolved_allow"
    assert reloaded_case.transaction_id == txn.id
    assert reloaded_case.transaction.state == "allowed"
    assert reloaded_case.gate_decision.decision == "hold"
    assert reloaded_case.gate_decision.semantic_assessment.risk_level == "high"
    assert reloaded_case.gate_decision.semantic_assessment.evidence_packet.transaction_id == txn.id
    assert {e.event_type for e in reloaded_case.audit_events} == {"case_opened", "case_resolved"}


def test_dataset_case_pairing_round_trip(db_session):
    legit = models.DatasetCase(
        split="dev",
        category="legitimate",
        drift_type="fast_spike",
        ground_truth_label="legitimate",
        rationale="one-time legitimate spend spike, same signal profile as the drift pair",
        fixture_path="fixtures/legitimate/pair_001.json",
    )
    db_session.add(legit)
    db_session.flush()

    drift = models.DatasetCase(
        split="dev",
        category="drift",
        drift_type="slow_drift",
        paired_with_id=legit.id,
        ground_truth_label="drift",
        rationale="slow drift into non-grocery spend, same signal profile as the legitimate pair",
        fixture_path="fixtures/drift/pair_001.json",
    )
    db_session.add(drift)
    db_session.flush()
    db_session.expire_all()

    reloaded = db_session.get(models.DatasetCase, drift.id)
    assert reloaded.paired_with_id == legit.id
