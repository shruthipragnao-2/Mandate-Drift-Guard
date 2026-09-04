"""add index on cases state

Revision ID: 8a80952b350f
Revises: 8e58ccd4981c
Create Date: 2026-09-04 12:36:09.593447

Flagged during Checkpoint C6 (cases table creation, 6dd89a9648d3) as a cheap addition to make
once a query actually needed it -- not urgent then, since nothing read `cases.state` yet.
Checkpoint C14's `GET /cases?state=hold` (Ops-analyst case queue) is that query: it filters
`cases` by `state` on every request. Written as its own revision on top of the current head,
not folded into 6dd89a9648d3 -- this project's established pattern (see 8e58ccd4981c's own
docstring) of never editing an already-applied migration in place.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8a80952b350f'
down_revision: Union[str, None] = '8e58ccd4981c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_cases_state", "cases", ["state"])


def downgrade() -> None:
    op.drop_index("ix_cases_state", table_name="cases")
