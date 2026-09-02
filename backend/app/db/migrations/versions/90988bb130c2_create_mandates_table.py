"""create mandates table

Revision ID: 90988bb130c2
Revises: 97d21cf4510e
Create Date: 2026-09-02 01:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '90988bb130c2'
down_revision: Union[str, None] = '97d21cf4510e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mandates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("budget", sa.Numeric(), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("allowed_categories", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mandates")
