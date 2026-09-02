"""Nullable-path tests (Checkpoint C6 / milestone M1).

Confirms the schema accepts the specific NULLs the fail-closed / nominal-ALLOW paths depend
on, per Decision 5 and the four traceability additions approved 2026-09-02.
"""

from app.db import models


def test_gate_decision_accepts_null_semantic_assessment_id(db_session, make_transaction):
    """Fail-closed path (timeout/malformed LLM output): no semantic_assessments row exists
    at all (Decision 5), so gate_decisions.semantic_assessment_id must accept NULL."""
    txn = make_transaction(state="held")
    gate_decision = models.GateDecision(
        semantic_assessment_id=None,
        transaction_id=txn.id,
        decision="hold",
        rule_version="v1",
        rule_applied="LLM call timed out -- fail-closed to HOLD",
    )
    db_session.add(gate_decision)
    db_session.flush()  # must not raise
    assert gate_decision.id is not None
    assert gate_decision.semantic_assessment_id is None


def test_audit_event_accepts_null_case_id(db_session, make_transaction):
    """Nominal-ALLOW path: most transactions never open a case, but the pipeline still
    writes one audit event for the pass (baseline §8/§K's nominal-path completeness note)."""
    txn = make_transaction(state="allowed")
    event = models.AuditEvent(
        case_id=None,
        mandate_id=txn.mandate_id,
        transaction_id=txn.id,
        event_type="evaluated_threshold_not_crossed",
        payload={"decision": "allow"},
    )
    db_session.add(event)
    db_session.flush()  # must not raise
    assert event.id is not None
    assert event.case_id is None


def test_audit_event_accepts_null_transaction_id(db_session, make_mandate):
    """Rare pre-persistence failure: an audit event can precede even the transaction row
    existing, so transaction_id must accept NULL (unlike mandate_id, which is NOT NULL)."""
    mandate = make_mandate()
    event = models.AuditEvent(
        case_id=None,
        mandate_id=mandate.id,
        transaction_id=None,
        event_type="pipeline_exception_before_transaction_persisted",
        payload={"error": "unhandled exception during ingestion validation"},
    )
    db_session.add(event)
    db_session.flush()  # must not raise
    assert event.id is not None
    assert event.transaction_id is None
