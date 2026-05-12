"""add dividends table

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-11
"""
import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dividends",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("amount_per_share", sa.Float(), nullable=False),
        sa.Column("total_amount", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dividends")),
    )
    op.create_index(
        "ix_dividends_ticker_ex_date", "dividends", ["ticker", "ex_date"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_dividends_ticker_ex_date", table_name="dividends")
    op.drop_table("dividends")
