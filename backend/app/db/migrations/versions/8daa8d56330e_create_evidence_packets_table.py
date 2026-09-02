"""create evidence_packets table

Revision ID: 8daa8d56330e
Revises: d87892027663
Create Date: 2026-09-02 01:45:02.000000

Includes `transaction_id` (NOT NULL) — approved 2026-09-02 as a traceability addition beyond
architecture §5's original column list (id, mandate_id, signals, trajectory, created_at only),
so every evidence packet is linkable back to the specific transaction that triggered it, not
just to its mandate.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '8daa8d56330e'
down_revision: Union[str, None] = 'd87892027663'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "evidence_packets",
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
        sa.Column("signals", postgresql.JSONB(), nullable=False),
        sa.Column("trajectory", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("evidence_packets")
