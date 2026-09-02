"""create audit_events table

Revision ID: 23ff2fa8647b
Revises: 6dd89a9648d3
Create Date: 2026-09-02 01:45:06.000000

`mandate_id` (NOT NULL) and `transaction_id` (nullable) are approved 2026-09-02 traceability
additions beyond architecture §5's original column list (id, case_id, event_type, payload,
created_at only) — `case_id` alone is null on the nominal-ALLOW path where no case is ever
opened, which would otherwise leave the most common audit events with no indexed link back to
the mandate/transaction they concern.

TODO (Decision 7, deferred past C6): the append-only DB-level enforcement for this table
(a second, grant-restricted DB role with INSERT/SELECT only — no UPDATE/DELETE) is explicitly
NOT implemented here. No second role or REVOKE statement exists yet anywhere in this project.
Track this for implementation before final submission.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '23ff2fa8647b'
down_revision: Union[str, None] = '6dd89a9648d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="RESTRICT"),
            nullable=True,
        ),
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
            nullable=True,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
