"""Shared fixtures for integration tests (Checkpoint C6 / milestone M1).

All integration tests here assume the real Postgres service from docker-compose.yml is
reachable at `app.config.settings.database_url` -- no mocking, no SQLite substitute, matching
the C5 precedent (docs/spec + backend/README.md) of verifying against the actual target
database rather than a lookalike.
"""

import pathlib
import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.config import settings
from app.db import models

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "app" / "db" / "migrations"))
    return cfg


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    return _alembic_config()


@pytest.fixture(scope="session", autouse=True)
def _schema_at_head(alembic_config):
    """Every integration test runs against the real Postgres service with the schema at the
    latest migration head. Applied once per test session, idempotently -- individual tests
    (e.g. test_migrations.py) may move the schema away from head temporarily, but must always
    restore it before returning control."""
    command.upgrade(alembic_config, "head")
    yield


@pytest.fixture(scope="session")
def engine():
    return sa.create_engine(settings.database_url)


@pytest.fixture
def db_session(engine):
    """One connection + outer transaction per test, rolled back at teardown -- keeps tests
    isolated from each other without recreating the schema for every test. Tests that expect
    a constraint violation should call `db_session.flush()` (not `.commit()`) so the failure
    surfaces without touching the outer transaction the fixture manages."""
    connection = engine.connect()
    transaction = connection.begin()
    # `create_savepoint`: the session's own flush/commit/rollback cycle operates on a nested
    # SAVEPOINT, not the outer connection-level transaction above -- so a test that triggers
    # an expected IntegrityError on flush() doesn't deassociate the outer transaction, and
    # teardown's rollback() always has a clean transaction to roll back.
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Minimal valid-row factories, chained mandate -> transaction -> evidence_packet ->
# semantic_assessment -> gate_decision -> case, each overridable per test.
# ---------------------------------------------------------------------------


@pytest.fixture
def make_mandate(db_session):
    def _make(**overrides):
        defaults = dict(
            purpose="weekly household groceries",
            budget=8000,
            period_days=7,
            allowed_categories=["groceries", "household essentials"],
        )
        defaults.update(overrides)
        mandate = models.Mandate(**defaults)
        db_session.add(mandate)
        db_session.flush()
        return mandate

    return _make


@pytest.fixture
def make_transaction(db_session, make_mandate):
    def _make(mandate=None, **overrides):
        mandate = mandate or make_mandate()
        defaults = dict(
            mandate_id=mandate.id,
            merchant="Test Merchant",
            category="groceries",
            amount=500,
            occurred_at=datetime.now(timezone.utc),
            idempotency_key=str(uuid.uuid4()),
            state="allowed",
        )
        defaults.update(overrides)
        txn = models.Transaction(**defaults)
        db_session.add(txn)
        db_session.flush()
        return txn

    return _make


@pytest.fixture
def make_evidence_packet(db_session, make_transaction):
    def _make(transaction=None, **overrides):
        transaction = transaction or make_transaction(state="held")
        defaults = dict(
            mandate_id=transaction.mandate_id,
            transaction_id=transaction.id,
            signals={"budget_utilization": 0.91, "spend_velocity": "elevated"},
            trajectory={"historical_distribution": "...", "current_distribution": "..."},
        )
        defaults.update(overrides)
        packet = models.EvidencePacket(**defaults)
        db_session.add(packet)
        db_session.flush()
        return packet

    return _make


@pytest.fixture
def make_semantic_assessment(db_session, make_evidence_packet):
    def _make(evidence_packet=None, **overrides):
        evidence_packet = evidence_packet or make_evidence_packet()
        defaults = dict(
            evidence_packet_id=evidence_packet.id,
            mandate_alignment="low",
            risk_level="high",
            confidence=0.91,
            evidence=["spend has shifted away from allowed categories"],
            raw_response={"mandate_alignment": "low", "risk_level": "high", "confidence": 0.91},
            model_version="claude-test-pin",
            prompt_version="v1",
            latency_ms=450,
        )
        defaults.update(overrides)
        assessment = models.SemanticAssessment(**defaults)
        db_session.add(assessment)
        db_session.flush()
        return assessment

    return _make


@pytest.fixture
def make_gate_decision(db_session, make_transaction):
    def _make(transaction=None, semantic_assessment=None, **overrides):
        transaction = transaction or make_transaction(state="held")
        defaults = dict(
            semantic_assessment_id=semantic_assessment.id if semantic_assessment else None,
            transaction_id=transaction.id,
            decision="hold",
            rule_version="v1",
            rule_applied="threshold crossed, routed to HOLD",
        )
        defaults.update(overrides)
        gate_decision = models.GateDecision(**defaults)
        db_session.add(gate_decision)
        db_session.flush()
        return gate_decision

    return _make


@pytest.fixture
def make_case(db_session, make_gate_decision):
    def _make(transaction=None, gate_decision=None, **overrides):
        if gate_decision is None:
            transaction = transaction or None
            gate_decision = make_gate_decision(transaction=transaction) if transaction else make_gate_decision()
        transaction = transaction or gate_decision.transaction
        defaults = dict(
            mandate_id=transaction.mandate_id,
            transaction_id=transaction.id,
            gate_decision_id=gate_decision.id,
            state="hold",
        )
        defaults.update(overrides)
        case = models.Case(**defaults)
        db_session.add(case)
        db_session.flush()
        return case

    return _make
