"""drop watchlist_items notes

Revision ID: 83cf7ac9e055
Revises: 0014
Create Date: 2026-05-29 23:18:59.359876

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '83cf7ac9e055'
down_revision: str | Sequence[str] | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("watchlist_items") as batch:
        batch.drop_column("notes")


def downgrade() -> None:
    with op.batch_alter_table("watchlist_items") as batch:
        batch.add_column(sa.Column("notes", sa.Text(), nullable=True))
