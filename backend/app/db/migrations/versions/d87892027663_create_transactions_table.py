"""create transactions table

Revision ID: d87892027663
Revises: 90988bb130c2
Create Date: 2026-09-02 01:45:01.000000

Decision 4 (docs/IMPLEMENTATION-BASELINE.md, 2026-09-02): `state` has exactly three durable
values (allowed / held / blocked). "pending_evaluation" is transient/in-pipeline-only and is
never written to Postgres, so it is deliberately excluded from this enum's DB-level values.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd87892027663'
down_revision: Union[str, None] = '90988bb130c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TRANSACTION_STATE_VALUES = ("allowed", "held", "blocked")


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "mandate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mandates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("merchant", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(*TRANSACTION_STATE_VALUES, name="transaction_state"),
            nullable=False,
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_transactions_idempotency_key"),
    )
    op.create_index(
        "ix_transactions_mandate_id_occurred_at", "transactions", ["mandate_id", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_mandate_id_occurred_at", table_name="transactions")
    op.drop_table("transactions")
    postgresql.ENUM(*TRANSACTION_STATE_VALUES, name="transaction_state").drop(op.get_bind(), checkfirst=True)
