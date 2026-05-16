"""add holdings sector

Revision ID: 6b48d3a5c80f
Revises: 0df4e23abe4e
Create Date: 2026-05-15 16:02:28.096416

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6b48d3a5c80f'
down_revision: str | Sequence[str] | None = '0df4e23abe4e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("holdings", sa.Column("sector", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("holdings", "sector")
