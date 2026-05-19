"""add ai_analyses strategy + strategy_version columns

Revision ID: 0009_aianalyses_strategy
Revises: cff08d913c3b
Create Date: 2026-05-18 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0009_aianalyses_strategy'
down_revision: str | Sequence[str] | None = 'cff08d913c3b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_analyses",
        sa.Column("strategy", sa.String(64), nullable=True),
    )
    op.add_column(
        "ai_analyses",
        sa.Column("strategy_version", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_analyses", "strategy_version")
    op.drop_column("ai_analyses", "strategy")
