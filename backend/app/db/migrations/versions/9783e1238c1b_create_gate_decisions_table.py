"""create gate_decisions table

Revision ID: 9783e1238c1b
Revises: 30509677b393
Create Date: 2026-09-02 01:45:04.000000

`semantic_assessment_id` is nullable — null on the fail-closed (timeout/malformed) path, per
Decision 5. `transaction_id` is NOT NULL and independent of that FK (approved 2026-09-02
traceability addition) so every gate decision stays linkable to its transaction even when the
LLM leg produced no semantic_assessments row at all.

`decision` has exactly two values (allow/hold) — architecture §5's exact parenthetical. BLOCK
is never a direct gate output; it is only reachable later via case resolution/timeout.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '9783e1238c1b'
down_revision: Union[str, None] = '30509677b393'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GATE_DECISION_VALUES = ("allow", "hold")


def upgrade() -> None:
    op.create_table(
        "gate_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "semantic_assessment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("semantic_assessments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "decision",
            sa.Enum(*GATE_DECISION_VALUES, name="gate_decision_value"),
            nullable=False,
        ),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("rule_applied", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("gate_decisions")
    postgresql.ENUM(*GATE_DECISION_VALUES, name="gate_decision_value").drop(op.get_bind(), checkfirst=True)
