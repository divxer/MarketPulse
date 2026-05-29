"""Phase Charter PR3a — paper_nav_snapshot immutable EOD NAV table.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-28
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_nav_snapshot",
        sa.Column("trading_date", sa.Date, primary_key=True),
        sa.Column("cash_balance", sa.Numeric(18, 6), nullable=False),
        sa.Column("holdings_mtm", sa.Numeric(18, 6), nullable=False),
        sa.Column("portfolio_nav", sa.Numeric(18, 6), nullable=False),
        sa.Column("anchor_portfolio_nav", sa.Numeric(18, 6), nullable=False),
        sa.Column("portfolio_index", sa.Numeric(18, 10), nullable=False),
        sa.Column("spy_close", sa.Numeric(18, 6), nullable=True),
        sa.Column("anchor_spy_close", sa.Numeric(18, 6), nullable=True),
        sa.Column("spy_index", sa.Numeric(18, 10), nullable=True),
        sa.Column("excess_return", sa.Numeric(18, 10), nullable=True),
        sa.Column("trading_days_observed", sa.Integer, nullable=False),
        sa.Column("coverage_ratio", sa.Numeric(18, 10), nullable=False),
        sa.Column("is_sufficient", sa.Boolean, nullable=False),
        sa.Column(
            "unpriced_positions_count", sa.Integer,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("unpriced_tickers", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_rebuilt", sa.Boolean,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("rebuild_reason", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("paper_nav_snapshot")
