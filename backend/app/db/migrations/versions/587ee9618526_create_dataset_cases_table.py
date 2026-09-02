"""create dataset_cases table

Revision ID: 587ee9618526
Revises: 23ff2fa8647b
Create Date: 2026-09-02 01:45:07.000000

Evaluation-harness fixture metadata (milestone M5 population; schema ships now with the rest
of M1). Independent of the live pipeline tables created by earlier revisions in this sequence.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '587ee9618526'
down_revision: Union[str, None] = '23ff2fa8647b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DATASET_SPLIT_VALUES = ("dev", "test")
DATASET_CATEGORY_VALUES = ("legitimate", "drift", "ambiguous")
DATASET_DRIFT_TYPE_VALUES = ("fast_spike", "slow_drift", "n_a")


def upgrade() -> None:
    op.create_table(
        "dataset_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("split", sa.Enum(*DATASET_SPLIT_VALUES, name="dataset_split"), nullable=False),
        sa.Column(
            "category", sa.Enum(*DATASET_CATEGORY_VALUES, name="dataset_category"), nullable=False
        ),
        sa.Column(
            "drift_type",
            sa.Enum(*DATASET_DRIFT_TYPE_VALUES, name="dataset_drift_type"),
            nullable=False,
        ),
        sa.Column(
            "paired_with_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_cases.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("ground_truth_label", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("fixture_path", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "paired_with_id IS NULL OR paired_with_id != id", name="ck_dataset_cases_no_self_pair"
        ),
    )


def downgrade() -> None:
    op.drop_table("dataset_cases")
    postgresql.ENUM(*DATASET_SPLIT_VALUES, name="dataset_split").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(*DATASET_CATEGORY_VALUES, name="dataset_category").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(*DATASET_DRIFT_TYPE_VALUES, name="dataset_drift_type").drop(
        op.get_bind(), checkfirst=True
    )
