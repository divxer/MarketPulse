"""add daily_recaps key_events_json

Revision ID: cff08d913c3b
Revises: 6b48d3a5c80f
Create Date: 2026-05-17 17:38:32.425336

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'cff08d913c3b'
down_revision: str | Sequence[str] | None = '6b48d3a5c80f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "daily_recaps",
        sa.Column("key_events_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("daily_recaps", "key_events_json")
