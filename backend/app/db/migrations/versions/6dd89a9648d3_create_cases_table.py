"""create cases table

Revision ID: 6dd89a9648d3
Revises: 9783e1238c1b
Create Date: 2026-09-02 01:45:05.000000

`transaction_id` (NOT NULL, unique) is Decision 2's required addition to architecture §5's
original cases table: HOLD applies to the specific triggering transaction, not just the
mandate. `state` uses the exact literal values architecture §5 gives:
"state (hold / resolved_allow / resolved_block)".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '6dd89a9648d3'
down_revision: Union[str, None] = '9783e1238c1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CASE_STATE_VALUES = ("hold", "resolved_allow", "resolved_block")


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "mandate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mandates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "gate_decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gate_decisions.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("state", sa.Enum(*CASE_STATE_VALUES, name="case_state"), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("transaction_id", name="uq_cases_transaction_id"),
    )


def downgrade() -> None:
    op.drop_table("cases")
    postgresql.ENUM(*CASE_STATE_VALUES, name="case_state").drop(op.get_bind(), checkfirst=True)
