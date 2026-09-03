"""Integration tests for the full pipeline orchestrator (Checkpoint C11,
`app.domain.pipeline.run_pipeline`). Mocked LLM client (fast, deterministic), REAL Postgres --
per this checkpoint's own instruction, the DB-atomicity/exact-rows-written behavior is the
actual thing under test, so a real database is required, not a fake.

One test per path:
  - immediate ALLOW (threshold not crossed)
  - HOLD (threshold crossed, LLM success with risk_level="high")
  - fail-closed HOLD on malformed LLM output (threshold crossed, LLM response fails schema)
Each asserts the exact DB rows written match what Plan §D/§K require -- including the
nominal-path audit event this checkpoint's instructions call out by name.
"""

from datetime import datetime, timedelta, timezone

from app.db import models
from app.domain.pipeline import IncomingTransaction, run_pipeline

# ---------------------------------------------------------------------------
# Mocked LLM client -- same shape as tests/unit/test_semantic_risk_client.py's fakes, kept
# local (not imported cross-directory) per this repo's existing convention of self-contained
# test files.
# ---------------------------------------------------------------------------


class _FakeToolUseBlock:
    def __init__(self, name, input_):
        self.type = "tool_use"
        self.name = name
        self.input = input_


class _FakeMessage:
    def __init__(self, content):
        self.content = content

    def model_dump(self, mode="json"):
        return {
            "content": [
                {"type": b.type, "name": getattr(b, "name", None), "input": getattr(b, "input", None)}
                for b in self.content
            ]
        }


class _FakeClient:
    """A minimal stand-in for `anthropic.Anthropic` -- only `.messages.create(...)` is ever
    called by `semantic_risk_client.assess`."""

    def __init__(self, response):
        self._response = response
        self.call_count = 0

        class _Messages:
            def create(inner_self, **kwargs):
                self.call_count += 1
                return self._response

        self.messages = _Messages()


def _valid_tool_input(**overrides):
    defaults = dict(mandate_alignment="high", risk_level="high", confidence=0.9, evidence=["spend pattern shifted"])
    defaults.update(overrides)
    return defaults


CREATED_AT = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _make_mandate(db_session, **overrides):
    defaults = dict(
        purpose="weekly household groceries",
        budget=1000.0,
        period_days=7,
        allowed_categories=["groceries"],
        created_at=CREATED_AT,
    )
    defaults.update(overrides)
    mandate = models.Mandate(**defaults)
    db_session.add(mandate)
    db_session.flush()
    return mandate


# ---------------------------------------------------------------------------
# Path 1: immediate ALLOW -- threshold not crossed
# ---------------------------------------------------------------------------


def test_immediate_allow_path_writes_exactly_the_expected_rows(db_session):
    mandate = _make_mandate(db_session, budget=100000.0)  # huge budget: velocity stays "normal"

    incoming = IncomingTransaction(
        merchant="Local Grocer",
        category="groceries",  # in allowed_categories -> category_shift "none"
        amount=10.0,  # tiny relative to budget -> velocity "normal"
        occurred_at=CREATED_AT + timedelta(days=1),
        idempotency_key="idem-allow-1",
    )

    result = run_pipeline(db_session, mandate, [], incoming, llm_client=_FakeClient(None))

    assert result.threshold_crossed is False
    assert result.state == "allowed"
    assert result.gate_decision is None
    assert result.case_id is None
    assert result.llm_status is None

    txn = db_session.get(models.Transaction, result.transaction_id)
    assert txn is not None
    assert txn.state == "allowed"

    assert db_session.query(models.EvidencePacket).filter_by(transaction_id=txn.id).count() == 0
    assert db_session.query(models.GateDecision).filter_by(transaction_id=txn.id).count() == 0
    assert db_session.query(models.Case).filter_by(transaction_id=txn.id).count() == 0

    audit_events = db_session.query(models.AuditEvent).filter_by(transaction_id=txn.id).all()
    assert len(audit_events) == 1
    assert audit_events[0].case_id is None
    assert audit_events[0].event_type == "evaluated_threshold_not_crossed"
    assert audit_events[0].payload["decision"] == "allow"


# ---------------------------------------------------------------------------
# Path 2: HOLD -- threshold crossed, LLM success with risk_level="high"
# ---------------------------------------------------------------------------


def test_hold_path_writes_exactly_the_expected_rows(db_session):
    mandate = _make_mandate(db_session, budget=100.0)  # small budget -> velocity trivially "critical"

    incoming = IncomingTransaction(
        merchant="Big Bulk Store",
        category="groceries",
        amount=5000.0,  # 50x the budget -> velocity "critical", crosses threshold
        occurred_at=CREATED_AT + timedelta(days=1),
        idempotency_key="idem-hold-1",
    )

    response = _FakeMessage([_FakeToolUseBlock("emit_risk_assessment", _valid_tool_input())])
    result = run_pipeline(db_session, mandate, [], incoming, llm_client=_FakeClient(response))

    assert result.threshold_crossed is True
    assert result.state == "held"
    assert result.gate_decision == "hold"
    assert result.llm_status == "success"
    assert result.case_id is not None

    txn = db_session.get(models.Transaction, result.transaction_id)
    assert txn.state == "held"

    evidence_packet = db_session.query(models.EvidencePacket).filter_by(transaction_id=txn.id).one()
    assert evidence_packet.signals["spend_velocity"] == "critical"

    semantic_assessment = (
        db_session.query(models.SemanticAssessment)
        .filter_by(evidence_packet_id=evidence_packet.id)
        .one()
    )
    assert semantic_assessment.risk_level == "high"
    assert semantic_assessment.mandate_alignment == "high"
    assert float(semantic_assessment.confidence) == 0.9

    gate_decision = db_session.query(models.GateDecision).filter_by(transaction_id=txn.id).one()
    assert gate_decision.decision == "hold"
    assert gate_decision.semantic_assessment_id == semantic_assessment.id

    case = db_session.query(models.Case).filter_by(transaction_id=txn.id).one()
    assert case.id == result.case_id
    assert case.state == "hold"
    assert case.gate_decision_id == gate_decision.id
    assert case.mandate_id == mandate.id

    audit_events = db_session.query(models.AuditEvent).filter_by(transaction_id=txn.id).all()
    assert len(audit_events) == 1
    assert audit_events[0].case_id == case.id
    assert audit_events[0].event_type == "evaluated_threshold_crossed"
    assert audit_events[0].payload["gate_decision"] == "hold"
    assert audit_events[0].payload["llm_status"] == "success"


# ---------------------------------------------------------------------------
# Path 3: fail-closed HOLD on malformed LLM output
# ---------------------------------------------------------------------------


def test_malformed_llm_output_fails_closed_to_hold_with_no_semantic_assessment_row(db_session):
    mandate = _make_mandate(db_session, budget=100.0)

    incoming = IncomingTransaction(
        merchant="Big Bulk Store",
        category="groceries",
        amount=5000.0,  # same trigger as the HOLD test -- crosses threshold
        occurred_at=CREATED_AT + timedelta(days=1),
        idempotency_key="idem-malformed-1",
    )

    bad_input = _valid_tool_input()
    del bad_input["evidence"]  # missing required field -> LlmOutput validation fails -> malformed
    response = _FakeMessage([_FakeToolUseBlock("emit_risk_assessment", bad_input)])
    result = run_pipeline(db_session, mandate, [], incoming, llm_client=_FakeClient(response))

    assert result.threshold_crossed is True
    assert result.state == "held"  # fail-closed, per baseline §6
    assert result.gate_decision == "hold"
    assert result.llm_status == "malformed"
    assert result.case_id is not None

    txn = db_session.get(models.Transaction, result.transaction_id)
    assert txn.state == "held"

    # Evidence packet IS still written -- it's built and persisted before the LLM call's
    # outcome is known to be malformed.
    evidence_packet = db_session.query(models.EvidencePacket).filter_by(transaction_id=txn.id).one()

    # Decision 5: no semantic_assessments row at all on a malformed response.
    assert (
        db_session.query(models.SemanticAssessment).filter_by(evidence_packet_id=evidence_packet.id).count() == 0
    )

    gate_decision = db_session.query(models.GateDecision).filter_by(transaction_id=txn.id).one()
    assert gate_decision.decision == "hold"
    assert gate_decision.semantic_assessment_id is None
    assert "fail_closed" in gate_decision.rule_applied

    case = db_session.query(models.Case).filter_by(transaction_id=txn.id).one()
    assert case.state == "hold"

    audit_events = db_session.query(models.AuditEvent).filter_by(transaction_id=txn.id).all()
    assert len(audit_events) == 1
    assert audit_events[0].payload["llm_status"] == "malformed"
    assert audit_events[0].payload["gate_decision"] == "hold"
