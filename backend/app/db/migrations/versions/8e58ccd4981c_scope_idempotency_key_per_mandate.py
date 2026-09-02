"""scope idempotency_key uniqueness per mandate

Revision ID: 8e58ccd4981c
Revises: 587ee9618526
Create Date: 2026-09-02 02:10:00.000000

Decision 8 (docs/IMPLEMENTATION-BASELINE.md, 2026-09-02): a global unique constraint on
transactions.idempotency_key risked two unrelated mandates' synthetic dataset cases colliding
on the same key string during the locked test-set batch run, silently dropping a transaction
and corrupting the pipeline-error-rate metric (eval-design §16, target 0). Scope uniqueness to
(mandate_id, idempotency_key) instead. Written as its own revision on top of the already-
applied C6 chain, not an edit to d87892027663_create_transactions_table.py in place.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8e58ccd4981c'
down_revision: Union[str, None] = '587ee9618526'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_transactions_idempotency_key", "transactions", type_="unique")
    op.create_unique_constraint(
        "uq_transactions_mandate_id_idempotency_key",
        "transactions",
        ["mandate_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_transactions_mandate_id_idempotency_key", "transactions", type_="unique"
    )
    op.create_unique_constraint(
        "uq_transactions_idempotency_key", "transactions", ["idempotency_key"]
    )
