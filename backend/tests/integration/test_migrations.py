"""Migration apply/rollback tests (Checkpoint C6 / milestone M1).

Exercises the full 97d21cf4510e -> ... -> 587ee9618526 revision chain against the real
Postgres service, both directions, mirroring the manual verification already run for this
checkpoint. Restores the schema to head in a `finally` block so a failed assertion here can't
leave the database mid-downgrade for every other integration test in the run.
"""

import pytest
import sqlalchemy as sa
from alembic import command

HEAD_REVISION = "8a80952b350f"

DOMAIN_TABLES = {
    "mandates",
    "transactions",
    "evidence_packets",
    "semantic_assessments",
    "gate_decisions",
    "cases",
    "audit_events",
    "dataset_cases",
}

ENUM_TYPES = {
    "transaction_state",
    "mandate_alignment",
    "gate_decision_value",
    "case_state",
    "dataset_split",
    "dataset_category",
    "dataset_drift_type",
}


def _tables(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("select tablename from pg_tables where schemaname = 'public'")
        ).fetchall()
    return {row[0] for row in rows}


def _enum_types(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text("select typname from pg_type where typtype = 'e'")).fetchall()
    return {row[0] for row in rows}


@pytest.mark.migration_roundtrip
def test_full_downgrade_then_upgrade_round_trip(engine, alembic_config):
    assert DOMAIN_TABLES.issubset(_tables(engine)), "schema must start at head (autouse fixture)"

    try:
        command.downgrade(alembic_config, "base")
        remaining = _tables(engine)
        assert not (DOMAIN_TABLES & remaining), (
            f"downgrade to base left domain tables behind: {DOMAIN_TABLES & remaining}"
        )
        assert not (ENUM_TYPES & _enum_types(engine)), "downgrade to base left enum types behind"

        command.upgrade(alembic_config, "head")
        rebuilt = _tables(engine)
        assert DOMAIN_TABLES.issubset(rebuilt), f"upgrade to head is missing tables: {DOMAIN_TABLES - rebuilt}"
        assert ENUM_TYPES.issubset(_enum_types(engine)), "upgrade to head is missing enum types"
    finally:
        # Guarantee every other integration test sees the schema at head, even if an
        # assertion above failed mid-way through the downgrade/upgrade cycle.
        command.upgrade(alembic_config, "head")


def test_head_matches_current_after_upgrade(engine, alembic_config):
    command.upgrade(alembic_config, "head")
    with engine.connect() as conn:
        current = conn.execute(sa.text("select version_num from alembic_version")).scalar()
    assert current == HEAD_REVISION
