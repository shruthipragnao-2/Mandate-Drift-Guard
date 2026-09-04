"""make cases gate_decision_id nullable

Revision ID: c4f1b7e2d9a3
Revises: 8a80952b350f
Create Date: 2026-09-04 18:12:44.108377

Decision 20 (docs/IMPLEMENTATION-BASELINE.md §24), resolving red-team finding RT-C1-008: the
fail-closed backstop around `domain.pipeline.run_pipeline` must open a case when the pipeline
throws an unforeseen exception, and Decision 20 states explicitly that this path writes NO
`gate_decisions` row -- the gate was never reached, so recording a decision it never made would
be a fabrication (the same reasoning Decision 5 already applies one layer up, where a malformed
LLM response produces no `semantic_assessments` row at all).

`cases.gate_decision_id` was created NOT NULL by 6dd89a9648d3, which makes "a case with no gate
decision" literally unrepresentable. This revision relaxes exactly that one column to NULLABLE.
Nothing else changes: the FK, the UNIQUE constraint on it, and every other column are left
alone, and no existing row is touched (every case written before this migration has, and keeps,
a gate decision).

Stated plainly, because it is a real weakening: this removes the schema-level guarantee that
every case has a gate decision, for ALL cases, in order to represent one new path. The
invariant that survives is narrower -- a case has a gate decision unless it was opened by the
Decision 20 exception backstop, in which case its audit event carries the reason instead. That
narrower invariant is enforced in application code and asserted by
tests/integration/test_pipeline_fail_closed_backstop.py, not by the schema.

Written as its own revision on top of the current head rather than edited into 6dd89a9648d3 --
this project's established pattern (see 8e58ccd4981c and 8a80952b350f) of never modifying an
already-applied migration in place.

The downgrade is deliberately NOT a blind re-tightening: rows written by the backstop have a
NULL here, and `ALTER COLUMN SET NOT NULL` would fail against them. Rather than delete or
invent data to make the downgrade succeed, it fails loudly with an explanation -- consistent
with this project's refusal to silently repair (Decision 3).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4f1b7e2d9a3'
down_revision: Union[str, None] = '8a80952b350f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "cases",
        "gate_decision_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    connection = op.get_bind()
    orphaned = connection.execute(
        sa.text("SELECT count(*) FROM cases WHERE gate_decision_id IS NULL")
    ).scalar_one()
    if orphaned:
        raise RuntimeError(
            f"cannot downgrade: {orphaned} case(s) were opened by the Decision 20 fail-closed "
            "exception backstop and legitimately have no gate_decisions row. Re-tightening this "
            "column would require deleting or fabricating audit-relevant rows. Decide what "
            "should happen to those cases first, explicitly, then downgrade."
        )
    op.alter_column(
        "cases",
        "gate_decision_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
