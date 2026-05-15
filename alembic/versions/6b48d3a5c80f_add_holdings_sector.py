"""add holdings sector

Revision ID: 6b48d3a5c80f
Revises: 0df4e23abe4e
Create Date: 2026-05-15 16:02:28.096416

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6b48d3a5c80f'
down_revision: Union[str, Sequence[str], None] = '0df4e23abe4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("holdings", sa.Column("sector", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("holdings", "sector")
