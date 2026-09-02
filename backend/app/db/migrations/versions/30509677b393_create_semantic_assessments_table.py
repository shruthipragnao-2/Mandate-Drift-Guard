"""create semantic_assessments table

Revision ID: 30509677b393
Revises: 8daa8d56330e
Create Date: 2026-09-02 01:45:03.000000

Decision 5 (2026-09-02): a row is written only when the full LLM response validates cleanly,
including `confidence` — so `confidence` is NOT NULL here; missing/malformed/unusable
confidence means no row at all (the malformed payload lives in audit_events.payload instead).

Decision 6 (2026-09-02): `risk_level` is TEXT, not a native Postgres ENUM — its low/medium/high
value set is enforced at the Pydantic layer only; no CHECK constraint is added here.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '30509677b393'
down_revision: Union[str, None] = '8daa8d56330e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MANDATE_ALIGNMENT_VALUES = ("low", "medium", "high")


def upgrade() -> None:
    op.create_table(
        "semantic_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "evidence_packet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_packets.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "mandate_alignment",
            sa.Enum(*MANDATE_ALIGNMENT_VALUES, name="mandate_alignment"),
            nullable=False,
        ),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("raw_response", postgresql.JSONB(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("semantic_assessments")
    postgresql.ENUM(*MANDATE_ALIGNMENT_VALUES, name="mandate_alignment").drop(op.get_bind(), checkfirst=True)
