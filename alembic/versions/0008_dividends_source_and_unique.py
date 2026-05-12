"""add dividends source + unique constraint + check

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-11
"""
import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add source column with server_default so existing rows get "manual".
    op.add_column(
        "dividends",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
    )

    # 2. Defensive dedup — older versions allowed duplicate (ticker, ex_date).
    # Keep the row with the smallest id (oldest by insert order).
    op.execute("""
        DELETE FROM dividends WHERE id NOT IN (
            SELECT MIN(id) FROM dividends GROUP BY ticker, ex_date
        )
    """)

    # 3. Add UNIQUE + CHECK. SQLite requires batch mode for ALTER TABLE ADD CONSTRAINT.
    with op.batch_alter_table("dividends") as batch:
        batch.create_unique_constraint(
            "uq_dividends_ticker_date", ["ticker", "ex_date"],
        )
        batch.create_check_constraint(
            "ck_dividends_amounts_non_negative",
            "amount_per_share >= 0 AND total_amount >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("dividends") as batch:
        batch.drop_constraint("ck_dividends_amounts_non_negative", type_="check")
        batch.drop_constraint("uq_dividends_ticker_date", type_="unique")
    op.drop_column("dividends", "source")
