"""add holdings columns to daily_recaps

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("daily_recaps") as batch:
        batch.add_column(sa.Column("holdings_overview_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("holdings_totals_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("daily_recaps") as batch:
        batch.drop_column("holdings_totals_json")
        batch.drop_column("holdings_overview_json")
