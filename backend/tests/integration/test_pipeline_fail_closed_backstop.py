"""Decision 20 (docs/IMPLEMENTATION-BASELINE.md §24) / red-team finding RT-C1-008: the
fail-closed exception backstop around `domain.pipeline.run_pipeline`.

Baseline §6's fail-closed invariant names four conditions that must route to HOLD. Three were
implemented in `policy_gate.decide()`; the fourth -- "unhandled pipeline exception" -- was
implemented nowhere, so an unforeseen throw became an HTTP 500 with nothing persisted at all.
These tests pin the behavior that closes it, against REAL Postgres (the exact set of rows
written, and the rollback of partial writes, is the thing under test -- a fake DB would prove
nothing about either).

The injected failure is a bare `RuntimeError` raised from inside the LLM call path,
deliberately chosen because it is NOT one of the four statuses `semantic_risk_client.assess()`
already handles (`success`/`timeout`/`malformed`/`transport_error`). Those four are structured
returns, not exceptions, and must keep flowing through the gate untouched -- the backstop is a
last resort, not a replacement for them. `test_structured_llm_failure_still_routes_through_the_gate`
below is the test that would fail if the backstop ever started swallowing them.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.domain.pipeline import IncomingTransaction, run_pipeline

CREATED_AT = datetime(2026, 8, 1, tzinfo=timezone.utc)


class _ExplodingClient:
    """Raises from `.messages.create(...)`, the one method `assess()` calls. `RuntimeError` is
    chosen precisely because nothing in the pipeline handles it: `assess()` catches the
    Anthropic SDK's own timeout/connection/API-status errors and returns a structured outcome
    for each, so a bare RuntimeError is a genuinely unanticipated failure -- which is exactly
    the class of thing RT-C1-008 was about."""

    def __init__(self, message="synthetic failure for the fail-closed backstop test"):
        self.call_count = 0

        class _Messages:
            def create(inner_self, **kwargs):
                self.call_count += 1
                raise RuntimeError(message)

        self.messages = _Messages()


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
    def __init__(self, response):
        self._response = response
        self.call_count = 0

        class _Messages:
            def create(inner_self, **kwargs):
                self.call_count += 1
                return self._response

        self.messages = _Messages()


@pytest.fixture
def committed_mandate(db_session):
    """A mandate that is COMMITTED, not merely flushed.

    This matters and is not test-scaffolding convenience. The backstop's first action is
    `session.rollback()` (Decision 20: partial writes from the failed attempt must not survive
    alongside the recovery state). In production the mandate was committed by an earlier
    request, so the rollback cannot touch it. Inside this suite's savepoint-scoped
    `db_session`, a merely-flushed mandate would be rolled back too, and the recovery insert
    would fail on the FK -- an artifact of the fixture, not of the code. Committing here makes
    the test's starting state match the real one it is standing in for.
    """
    mandate = models.Mandate(
        purpose="weekly household groceries",
        budget=1000.0,
        period_days=7,
        allowed_categories=["groceries"],
        created_at=CREATED_AT,
    )
    db_session.add(mandate)
    db_session.commit()
    return mandate


def _crossing_transaction(**overrides):
    """Amount large against a 1000 budget one day into a 7-day period -> velocity well past
    its band maximum, so the threshold crosses and the LLM leg is actually reached."""
    defaults = dict(
        merchant="Test Merchant",
        category="groceries",
        amount=900.0,
        occurred_at=CREATED_AT + timedelta(days=1),
        idempotency_key="idem-backstop-1",
    )
    defaults.update(overrides)
    return IncomingTransaction(**defaults)


# ---------------------------------------------------------------------------
# The invariant itself
# ---------------------------------------------------------------------------


def test_unhandled_exception_routes_to_hold_with_a_traceable_audit_event(
    db_session, committed_mandate
):
    """The whole of RT-C1-008 in one assertion set: the pipeline throws, and instead of a 500
    with nothing persisted, there is a held transaction, an open case, and an audit event
    naming the reason."""
    client = _ExplodingClient()

    result = run_pipeline(
        db_session, committed_mandate, [], _crossing_transaction(), llm_client=client
    )

    assert client.call_count == 1, "the exploding client must actually have been reached"

    # Held, not allowed -- baseline §6's invariant.
    assert result.state == "held"
    assert result.fail_closed_reason == "RuntimeError"
    assert result.case_id is not None
    # The gate genuinely never ran, and the result says so rather than inventing a decision.
    assert result.gate_decision is None

    txn = db_session.get(models.Transaction, result.transaction_id)
    assert txn is not None, "nothing persisted at all was the actual RT-C1-008 bug"
    assert txn.state == "held"

    case = db_session.get(models.Case, result.case_id)
    assert case.state == "hold"
    assert case.transaction_id == txn.id
    assert case.gate_decision_id is None

    events = (
        db_session.query(models.AuditEvent)
        .filter_by(case_id=case.id, event_type="pipeline_exception_fail_closed_hold")
        .all()
    )
    assert len(events) == 1
    payload = events[0].payload
    assert payload["reason"] == "unhandled_pipeline_exception"
    assert payload["exception_type"] == "RuntimeError"
    assert payload["gate_reached"] is False
    assert events[0].transaction_id == txn.id
    assert events[0].mandate_id == committed_mandate.id


def test_no_semantic_assessment_or_gate_decision_row_is_written(db_session, committed_mandate):
    """Decision 20 mirrors Decision 5's "no row when nothing validated", one layer earlier.
    Nothing valid was produced and the gate was never reached, so neither row exists -- writing
    one would put a decision in the audit log that was never made."""
    result = run_pipeline(
        db_session, committed_mandate, [], _crossing_transaction(), llm_client=_ExplodingClient()
    )

    txn_id = result.transaction_id
    assert db_session.query(models.GateDecision).filter_by(transaction_id=txn_id).count() == 0
    assert (
        db_session.query(models.SemanticAssessment)
        .join(models.EvidencePacket)
        .filter(models.EvidencePacket.transaction_id == txn_id)
        .count()
        == 0
    )


def test_partial_writes_from_the_failed_attempt_do_not_survive(db_session, committed_mandate):
    """The rollback requirement. The exception is injected AFTER the pipeline has already
    flushed rows in its own attempt, so this distinguishes a real rollback from a backstop that
    merely adds new rows on top of a half-written evaluation.

    The failure is injected into `_persist_crossed_case` itself, at the point where it has
    already flushed a transaction and an evidence packet -- monkeypatching `models.GateDecision`
    to explode means those two flushes really happened before the throw.
    """
    import app.domain.pipeline as pipeline_module

    response = _FakeMessage(
        [
            _FakeToolUseBlock(
                "emit_risk_assessment",
                dict(mandate_alignment="high", risk_level="high", confidence=0.9, evidence=["x"]),
            )
        ]
    )

    real_gate_decision = pipeline_module.models.GateDecision

    def _explode(*args, **kwargs):
        raise RuntimeError("synthetic failure after partial flushes")

    pipeline_module.models.GateDecision = _explode
    try:
        result = run_pipeline(
            db_session,
            committed_mandate,
            [],
            _crossing_transaction(),
            llm_client=_FakeClient(response),
        )
    finally:
        pipeline_module.models.GateDecision = real_gate_decision

    assert result.state == "held"
    assert result.fail_closed_reason == "RuntimeError"

    # Exactly one transaction for this mandate: the recovery one. The transaction and evidence
    # packet flushed by the failed attempt were rolled back, not left behind.
    txns = (
        db_session.query(models.Transaction)
        .filter_by(mandate_id=committed_mandate.id)
        .all()
    )
    assert len(txns) == 1
    assert txns[0].id == result.transaction_id
    assert txns[0].state == "held"

    assert (
        db_session.query(models.EvidencePacket)
        .filter_by(mandate_id=committed_mandate.id)
        .count()
        == 0
    ), "the failed attempt's evidence packet must not coexist with the fail-closed hold"


# ---------------------------------------------------------------------------
# What the audit event is allowed to say
# ---------------------------------------------------------------------------


def test_audit_event_does_not_echo_request_data_verbatim(db_session, committed_mandate):
    """Decision 20: exception type and message only, never request data echoed back verbatim.

    The injected exception message embeds every request-supplied field, which is exactly what a
    real library exception does when it quotes the input it choked on (RT-C1-004's
    `InvalidTextRepresentation` did precisely this). Each must be substituted out before the
    message reaches a persisted, human-readable audit record.
    """
    merchant = "SuspiciousMerchantName"
    category = "unusual-category-tag"
    idempotency_key = "idem-leaky-key-12345"
    amount = 912.34

    incoming = _crossing_transaction(
        merchant=merchant, category=category, amount=amount, idempotency_key=idempotency_key
    )
    leaky = (
        f"failed on merchant={merchant} category={category} "
        f"amount={amount} key={idempotency_key} at {incoming.occurred_at.isoformat()}"
    )

    result = run_pipeline(
        db_session, committed_mandate, [], incoming, llm_client=_ExplodingClient(leaky)
    )

    event = (
        db_session.query(models.AuditEvent)
        .filter_by(case_id=result.case_id, event_type="pipeline_exception_fail_closed_hold")
        .one()
    )
    message = event.payload["exception_message"]

    for leaked in (merchant, category, idempotency_key, str(amount), incoming.occurred_at.isoformat()):
        assert leaked not in message, f"{leaked!r} was echoed verbatim into the audit record"

    # Redacted, not merely dropped: the event still says what kind of failure this was, or it
    # would be useless to the analyst it exists for.
    assert event.payload["exception_type"] == "RuntimeError"
    assert "[redacted:merchant]" in message
    assert "[redacted:idempotency_key]" in message


def test_audit_event_never_carries_a_traceback(db_session, committed_mandate):
    result = run_pipeline(
        db_session, committed_mandate, [], _crossing_transaction(), llm_client=_ExplodingClient()
    )
    event = (
        db_session.query(models.AuditEvent)
        .filter_by(case_id=result.case_id, event_type="pipeline_exception_fail_closed_hold")
        .one()
    )
    serialized = str(event.payload)
    assert "Traceback" not in serialized
    assert "File \"" not in serialized
    assert "traceback" not in event.payload


def test_long_exception_message_is_truncated(db_session, committed_mandate):
    result = run_pipeline(
        db_session,
        committed_mandate,
        [],
        _crossing_transaction(),
        llm_client=_ExplodingClient("x" * 5000),
    )
    event = (
        db_session.query(models.AuditEvent)
        .filter_by(case_id=result.case_id, event_type="pipeline_exception_fail_closed_hold")
        .one()
    )
    assert len(event.payload["exception_message"]) < 600
    assert event.payload["exception_message"].endswith("... [truncated]")


# ---------------------------------------------------------------------------
# What the backstop must NOT swallow
# ---------------------------------------------------------------------------


def test_structured_llm_failure_still_routes_through_the_gate(db_session, committed_mandate):
    """The four already-implemented fail-closed paths must be untouched by the backstop.

    A malformed LLM response is a STRUCTURED outcome, not an exception -- it reaches the gate,
    which holds it and records a real gate decision with a rule. If the backstop ever started
    catching these, this case would lose its gate_decisions row and its evidence packet, and
    the audit record would degrade from "the gate held this, by this rule" to "something
    threw". That is the regression this test exists to catch.
    """
    malformed = _FakeMessage(
        [_FakeToolUseBlock("emit_risk_assessment", {"mandate_alignment": "not-a-valid-value"})]
    )

    result = run_pipeline(
        db_session, committed_mandate, [], _crossing_transaction(), llm_client=_FakeClient(malformed)
    )

    assert result.state == "held"
    assert result.fail_closed_reason is None, "this is a gate decision, not a backstop catch"
    assert result.gate_decision == "hold"
    assert result.llm_status == "malformed"

    case = db_session.get(models.Case, result.case_id)
    assert case.gate_decision_id is not None, "the gate ran, so its decision must be recorded"
    assert (
        db_session.query(models.EvidencePacket)
        .filter_by(transaction_id=result.transaction_id)
        .count()
        == 1
    )


def test_integrity_error_is_not_swallowed(db_session, committed_mandate):
    """RT-C1-009's fix lives in `api/transactions.py` and depends on `IntegrityError` reaching
    it. If the backstop caught it, that handler would go dead AND the recovery insert would
    violate the same unique constraint it was reacting to -- so this one exception type is
    deliberately re-raised.
    """
    incoming = _crossing_transaction(idempotency_key="idem-duplicate")

    db_session.add(
        models.Transaction(
            mandate_id=committed_mandate.id,
            merchant="Prior",
            category="groceries",
            amount=10.0,
            occurred_at=CREATED_AT + timedelta(hours=1),
            idempotency_key="idem-duplicate",
            state="allowed",
        )
    )
    db_session.commit()

    response = _FakeMessage(
        [
            _FakeToolUseBlock(
                "emit_risk_assessment",
                dict(mandate_alignment="high", risk_level="high", confidence=0.9, evidence=["x"]),
            )
        ]
    )

    with pytest.raises(IntegrityError):
        run_pipeline(
            db_session, committed_mandate, [], incoming, llm_client=_FakeClient(response)
        )


# ---------------------------------------------------------------------------
# The reporting layer (the fail-closed hold must not describe itself as an allow)
# ---------------------------------------------------------------------------


def test_case_detail_endpoint_renders_a_backstop_case_without_erroring(
    db_session, committed_mandate, api_client
):
    """`GET /cases/{id}` used to assert an evidence packet existed via `.one()` and to
    dereference `case.gate_decision` unconditionally. A backstop case has neither, so reading
    one would have produced a second 500 -- at the exact moment an analyst is trying to look at
    the case the first failure created."""
    result = run_pipeline(
        db_session, committed_mandate, [], _crossing_transaction(), llm_client=_ExplodingClient()
    )

    response = api_client.get(
        f"/cases/{result.case_id}", headers={"Authorization": "Bearer test-bearer-token"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "hold"
    assert body["gate_decision"] is None
    assert body["evidence_packet"] is None
    assert body["semantic_assessment"] is None
    assert body["fail_closed_reason"] == "RuntimeError"
    assert body["transaction"]["state"] == "held"


def test_backstop_case_appears_in_the_ops_queue(db_session, committed_mandate, api_client):
    """Decision 20 requires it to surface "like any other hold" -- so the queue endpoint, which
    an analyst actually works from, must list it."""
    result = run_pipeline(
        db_session, committed_mandate, [], _crossing_transaction(), llm_client=_ExplodingClient()
    )

    response = api_client.get(
        "/cases?state=hold", headers={"Authorization": "Bearer test-bearer-token"}
    )

    assert response.status_code == 200
    assert str(result.case_id) in [c["id"] for c in response.json()["cases"]]


def test_ingestion_endpoint_never_reports_allow_for_a_fail_closed_hold(
    db_session, committed_mandate, api_client, monkeypatch
):
    """The reporting fail-open this decision would otherwise have introduced.

    `POST /transactions` computed `decision = result.gate_decision or "allow"`. That default was
    correct while "no gate decision" could only mean "the threshold was never crossed". The
    backstop makes it also mean "the gate never ran and we HELD the money", so the endpoint
    would have answered `state="held"` and `decision="allow"` in the same response -- the
    pipeline failing closed while the API told the caller it had allowed the transaction.

    Injected at `_default_client` so the exception travels the real route: the endpoint's own
    call into `run_pipeline`, with no `llm_client` passed, exactly as production runs it.
    """
    import app.domain.semantic_risk_client as src

    monkeypatch.setattr(src, "_default_client", lambda app_settings: _ExplodingClient())

    response = api_client.post(
        "/transactions",
        headers={"Authorization": "Bearer test-bearer-token"},
        json={
            "mandate_id": str(committed_mandate.id),
            "merchant": "Test Merchant",
            "category": "groceries",
            "amount": 900.0,
            "occurred_at": (CREATED_AT + timedelta(days=1)).isoformat(),
            "idempotency_key": "idem-api-backstop",
        },
    )

    assert response.status_code == 200, "an unforeseen pipeline exception must not be a 500"
    body = response.json()
    assert body["state"] == "held"
    assert body["decision"] == "hold", "a held transaction must never report decision=allow"
    assert body["gate_decision"] is None
    assert body["case_id"] is not None
