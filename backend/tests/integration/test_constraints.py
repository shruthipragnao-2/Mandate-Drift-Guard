"""NOT NULL, uniqueness, FK-violation, and ON DELETE RESTRICT tests (Checkpoint C6 / M1).

Constraint violations are asserted by calling `db_session.flush()` (never `.commit()`) so the
failure surfaces from Postgres without touching the fixture-managed outer transaction; the
`db_session` fixture rolls everything back at teardown regardless of outcome.
"""

import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
import sqlalchemy.exc

from app.db import models


def _core_delete(db_session, model, row_id):
    """Issue a Core-level DELETE, bypassing the ORM's own relationship-maintenance behavior
    (which would otherwise UPDATE a nullable child FK to NULL before deleting the parent,
    masking the database's own ON DELETE RESTRICT clause -- see the ON DELETE RESTRICT
    section below for why every one of those tests goes through this helper instead of
    `db_session.delete(instance)`)."""
    db_session.execute(sa.delete(model).where(model.id == row_id))

# ---------------------------------------------------------------------------
# NOT NULL
# ---------------------------------------------------------------------------


def test_mandate_purpose_not_null(db_session):
    mandate = models.Mandate(purpose=None, budget=8000, period_days=7, allowed_categories=["groceries"])
    db_session.add(mandate)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


def test_transaction_mandate_id_not_null(db_session):
    txn = models.Transaction(
        mandate_id=None,
        merchant="Test Merchant",
        category="groceries",
        amount=500,
        occurred_at=datetime.now(timezone.utc),
        idempotency_key=str(uuid.uuid4()),
        state="allowed",
    )
    db_session.add(txn)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


def test_evidence_packet_transaction_id_not_null(db_session, make_transaction):
    txn = make_transaction(state="held")
    packet = models.EvidencePacket(
        mandate_id=txn.mandate_id,
        transaction_id=None,
        signals={},
        trajectory={},
    )
    db_session.add(packet)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


def test_gate_decision_transaction_id_not_null(db_session):
    gate_decision = models.GateDecision(
        semantic_assessment_id=None,
        transaction_id=None,
        decision="hold",
        rule_version="v1",
        rule_applied="fail-closed",
    )
    db_session.add(gate_decision)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


def test_semantic_assessment_confidence_not_null(db_session, make_evidence_packet):
    """Decision 5: confidence is NOT NULL -- a row only ever exists when the full response,
    including confidence, validated cleanly."""
    packet = make_evidence_packet()
    assessment = models.SemanticAssessment(
        evidence_packet_id=packet.id,
        mandate_alignment="low",
        risk_level="high",
        confidence=None,
        evidence=["..."],
        raw_response={},
        model_version="claude-test-pin",
        prompt_version="v1",
        latency_ms=100,
    )
    db_session.add(assessment)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


def test_case_transaction_id_not_null(db_session, make_gate_decision):
    gate_decision = make_gate_decision()
    case = models.Case(
        mandate_id=gate_decision.transaction.mandate_id,
        transaction_id=None,
        gate_decision_id=gate_decision.id,
        state="hold",
    )
    db_session.add(case)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


def test_audit_event_mandate_id_not_null(db_session):
    event = models.AuditEvent(case_id=None, mandate_id=None, transaction_id=None, event_type="test", payload={})
    db_session.add(event)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------


def test_idempotency_key_unique_within_same_mandate(db_session, make_mandate, make_transaction):
    """Decision 8: idempotency_key uniqueness is scoped per mandate -- the same key reused
    for the same mandate must still be rejected."""
    mandate = make_mandate()
    shared_key = str(uuid.uuid4())
    make_transaction(mandate=mandate, idempotency_key=shared_key)

    dup = models.Transaction(
        mandate_id=mandate.id,
        merchant="Other Merchant",
        category="groceries",
        amount=100,
        occurred_at=datetime.now(timezone.utc),
        idempotency_key=shared_key,
        state="allowed",
    )
    db_session.add(dup)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


def test_idempotency_key_allowed_across_different_mandates(db_session, make_mandate, make_transaction):
    """Decision 8 (2026-09-02): uniqueness is per-mandate, not global -- two unrelated
    mandates reusing the same key string (e.g. two synthetic dataset cases colliding on a
    generated key) must NOT collide."""
    mandate_a = make_mandate()
    mandate_b = make_mandate()
    shared_key = str(uuid.uuid4())

    make_transaction(mandate=mandate_a, idempotency_key=shared_key)

    other = models.Transaction(
        mandate_id=mandate_b.id,
        merchant="Other Merchant",
        category="groceries",
        amount=100,
        occurred_at=datetime.now(timezone.utc),
        idempotency_key=shared_key,
        state="allowed",
    )
    db_session.add(other)
    db_session.flush()  # must not raise
    assert other.id is not None


def test_cases_transaction_id_unique(db_session, make_case, make_gate_decision):
    case = make_case()

    second_gate_decision = make_gate_decision(transaction=case.transaction)
    dup_case = models.Case(
        mandate_id=case.mandate_id,
        transaction_id=case.transaction_id,
        gate_decision_id=second_gate_decision.id,
        state="hold",
    )
    db_session.add(dup_case)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


# ---------------------------------------------------------------------------
# FK violation (referencing a row that doesn't exist)
# ---------------------------------------------------------------------------


def test_transaction_rejects_unknown_mandate_id(db_session):
    txn = models.Transaction(
        mandate_id=uuid.uuid4(),
        merchant="Test Merchant",
        category="groceries",
        amount=500,
        occurred_at=datetime.now(timezone.utc),
        idempotency_key=str(uuid.uuid4()),
        state="allowed",
    )
    db_session.add(txn)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


def test_evidence_packet_rejects_unknown_transaction_id(db_session, make_mandate):
    mandate = make_mandate()
    packet = models.EvidencePacket(
        mandate_id=mandate.id, transaction_id=uuid.uuid4(), signals={}, trajectory={}
    )
    db_session.add(packet)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


def test_case_rejects_unknown_gate_decision_id(db_session, make_transaction):
    txn = make_transaction(state="held")
    case = models.Case(
        mandate_id=txn.mandate_id, transaction_id=txn.id, gate_decision_id=uuid.uuid4(), state="hold"
    )
    db_session.add(case)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


def test_dataset_case_rejects_unknown_paired_with_id(db_session):
    case = models.DatasetCase(
        split="dev",
        category="drift",
        drift_type="slow_drift",
        paired_with_id=uuid.uuid4(),
        ground_truth_label="drift",
        rationale="synthetic slow drift into non-grocery spend",
        fixture_path="fixtures/drift/case_001.json",
    )
    db_session.add(case)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()


# ---------------------------------------------------------------------------
# ON DELETE RESTRICT -- every FK in the schema
#
# These all go through `_core_delete` (a Core-level DELETE), not `db_session.delete(obj)`.
# The ORM's default relationship handling would otherwise UPDATE a nullable child FK to NULL
# before issuing the parent DELETE, to keep the mapped relationship consistent -- which
# "succeeds" at deleting the parent but proves nothing about the database's own RESTRICT
# clause. A Core-level DELETE has no relationship bookkeeping to fall back on, so it exercises
# the FK constraint directly, the same way a delete issued by non-ORM code would.
# ---------------------------------------------------------------------------


@pytest.fixture
def full_hold_chain(make_case):
    """mandate -> transaction(held) -> evidence_packet -> semantic_assessment -> gate_decision
    -> case, i.e. every live-pipeline FK populated at once (excluding audit_events, added
    per-test below since it's the leaf of the chain)."""
    case = make_case()
    return case


def test_delete_mandate_restricted_by_children(db_session, full_hold_chain):
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        _core_delete(db_session, models.Mandate, full_hold_chain.mandate_id)


def test_delete_transaction_restricted_by_children(db_session, full_hold_chain):
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        _core_delete(db_session, models.Transaction, full_hold_chain.transaction_id)


def test_delete_evidence_packet_restricted_by_semantic_assessment(
    db_session, make_semantic_assessment
):
    assessment = make_semantic_assessment()
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        _core_delete(db_session, models.EvidencePacket, assessment.evidence_packet_id)


def test_delete_semantic_assessment_restricted_by_gate_decision(
    db_session, make_semantic_assessment, make_gate_decision
):
    assessment = make_semantic_assessment()
    make_gate_decision(transaction=assessment.evidence_packet.transaction, semantic_assessment=assessment)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        _core_delete(db_session, models.SemanticAssessment, assessment.id)


def test_delete_gate_decision_restricted_by_case(db_session, full_hold_chain):
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        _core_delete(db_session, models.GateDecision, full_hold_chain.gate_decision_id)


def test_delete_case_restricted_by_audit_event(db_session, full_hold_chain):
    case = full_hold_chain
    event = models.AuditEvent(
        case_id=case.id,
        mandate_id=case.mandate_id,
        transaction_id=case.transaction_id,
        event_type="case_opened",
        payload={"state": "hold"},
    )
    db_session.add(event)
    db_session.flush()

    with pytest.raises(sqlalchemy.exc.IntegrityError):
        _core_delete(db_session, models.Case, case.id)


def test_delete_dataset_case_restricted_by_pairing(db_session):
    case_a = models.DatasetCase(
        split="dev",
        category="legitimate",
        drift_type="fast_spike",
        ground_truth_label="legitimate",
        rationale="one-time legitimate spike",
        fixture_path="fixtures/legitimate/case_001.json",
    )
    db_session.add(case_a)
    db_session.flush()

    case_b = models.DatasetCase(
        split="dev",
        category="drift",
        drift_type="slow_drift",
        paired_with_id=case_a.id,
        ground_truth_label="drift",
        rationale="paired slow-drift counterpart",
        fixture_path="fixtures/drift/case_001.json",
    )
    db_session.add(case_b)
    db_session.flush()

    with pytest.raises(sqlalchemy.exc.IntegrityError):
        _core_delete(db_session, models.DatasetCase, case_a.id)
