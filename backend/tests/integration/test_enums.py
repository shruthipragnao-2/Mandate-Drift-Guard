"""Enum-value rejection tests (Checkpoint C6 / milestone M1).

Only the four columns backed by a native Postgres ENUM type are covered here.
`semantic_assessments.risk_level` is deliberately excluded -- Decision 6 stores it as TEXT
with no DB-level value constraint, so there is nothing for Postgres to reject; its value set
is validated at the Pydantic layer in a later milestone, not here.
"""

import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy.exc

from app.db import models


def test_transaction_state_rejects_invalid_value(db_session, make_mandate):
    mandate = make_mandate()
    txn = models.Transaction(
        mandate_id=mandate.id,
        merchant="Test Merchant",
        category="groceries",
        amount=500,
        occurred_at=datetime.now(timezone.utc),
        idempotency_key=str(uuid.uuid4()),
        state="pending_evaluation",  # Decision 4: never a durable value
    )
    db_session.add(txn)
    with pytest.raises(sqlalchemy.exc.DBAPIError):
        db_session.flush()


def test_mandate_alignment_rejects_invalid_value(db_session, make_evidence_packet):
    packet = make_evidence_packet()
    assessment = models.SemanticAssessment(
        evidence_packet_id=packet.id,
        mandate_alignment="extreme",  # only low/medium/high are valid
        risk_level="high",
        confidence=0.9,
        evidence=["..."],
        raw_response={},
        model_version="claude-test-pin",
        prompt_version="v1",
        latency_ms=100,
    )
    db_session.add(assessment)
    with pytest.raises(sqlalchemy.exc.DBAPIError):
        db_session.flush()


def test_gate_decision_rejects_invalid_value(db_session, make_transaction):
    txn = make_transaction(state="held")
    gate_decision = models.GateDecision(
        semantic_assessment_id=None,
        transaction_id=txn.id,
        decision="block",  # BLOCK is never a direct gate output -- only allow/hold are valid
        rule_version="v1",
        rule_applied="n/a",
    )
    db_session.add(gate_decision)
    with pytest.raises(sqlalchemy.exc.DBAPIError):
        db_session.flush()


def test_case_state_rejects_invalid_value(db_session, make_gate_decision):
    gate_decision = make_gate_decision()
    case = models.Case(
        mandate_id=gate_decision.transaction.mandate_id,
        transaction_id=gate_decision.transaction_id,
        gate_decision_id=gate_decision.id,
        state="pending",  # only hold/resolved_allow/resolved_block are valid
    )
    db_session.add(case)
    with pytest.raises(sqlalchemy.exc.DBAPIError):
        db_session.flush()
