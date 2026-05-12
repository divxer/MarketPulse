"""add stock_splits table

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-11
"""
import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_splits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("ratio", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_splits")),
        sa.UniqueConstraint("ticker", "ex_date", name="uq_stock_splits_ticker_date"),
        sa.CheckConstraint("ratio > 0 AND ratio != 1", name="ck_stock_splits_ratio_valid"),
    )
    op.create_index(
        "ix_stock_splits_ticker_ex_date", "stock_splits", ["ticker", "ex_date"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_stock_splits_ticker_ex_date", table_name="stock_splits")
    op.drop_table("stock_splits")
