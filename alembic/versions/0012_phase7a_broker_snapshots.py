"""Phase 7a broker snapshot tables.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-23
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broker_sync_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("broker", sa.String(16), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_type", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
        sa.CheckConstraint("broker IN ('IBKR')", name="ck_broker_sync_run_broker"),
        sa.CheckConstraint(
            "broker_environment IN ('paper', 'live', 'unknown')",
            name="ck_broker_sync_run_environment",
        ),
        sa.CheckConstraint(
            "status IN ('started', 'completed', 'failed')",
            name="ck_broker_sync_run_status",
        ),
    )
    op.create_index("ix_broker_sync_run_started", "broker_sync_run", ["started_at"])
    op.create_index(
        "ix_broker_sync_run_status_started",
        "broker_sync_run",
        ["status", "started_at"],
    )

    op.create_table(
        "broker_account_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_run_id", sa.Integer(), sa.ForeignKey("broker_sync_run.id"), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_type", sa.String(64), nullable=True),
        sa.Column("base_currency", sa.String(8), nullable=True),
        sa.Column("net_liquidation", sa.Numeric(18, 6), nullable=True),
        sa.Column("buying_power", sa.Numeric(18, 6), nullable=True),
        sa.Column("maintenance_margin", sa.Numeric(18, 6), nullable=True),
        sa.Column("excess_liquidity", sa.Numeric(18, 6), nullable=True),
    )
    op.create_index("ix_broker_account_sync", "broker_account_snapshot", ["sync_run_id"])

    op.create_table(
        "broker_cash_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_run_id", sa.Integer(), sa.ForeignKey("broker_sync_run.id"), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("cash_balance", sa.Numeric(18, 6), nullable=True),
        sa.Column("settled_cash", sa.Numeric(18, 6), nullable=True),
        sa.Column("accrued_interest", sa.Numeric(18, 6), nullable=True),
    )
    op.create_index("ix_broker_cash_sync", "broker_cash_snapshot", ["sync_run_id"])
    op.create_index(
        "ix_broker_cash_account_currency",
        "broker_cash_snapshot",
        ["account_id", "currency"],
    )

    op.create_table(
        "broker_position_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_run_id", sa.Integer(), sa.ForeignKey("broker_sync_run.id"), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("asset_class", sa.String(32), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("avg_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("market_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("market_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("unrealized_pnl", sa.Numeric(18, 6), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(18, 6), nullable=True),
    )
    op.create_index("ix_broker_position_sync", "broker_position_snapshot", ["sync_run_id"])
    op.create_index(
        "ix_broker_position_account_symbol",
        "broker_position_snapshot",
        ["account_id", "symbol"],
    )

    op.create_table(
        "broker_open_order_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_run_id", sa.Integer(), sa.ForeignKey("broker_sync_run.id"), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("broker_order_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("side", sa.String(8), nullable=True),
        sa.Column("order_type", sa.String(32), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("limit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("status", sa.String(64), nullable=True),
    )
    op.create_index("ix_broker_open_order_sync", "broker_open_order_snapshot", ["sync_run_id"])
    op.create_index(
        "ix_broker_open_order_account_order",
        "broker_open_order_snapshot",
        ["account_id", "broker_order_id"],
    )

    op.create_table(
        "broker_execution_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_run_id", sa.Integer(), sa.ForeignKey("broker_sync_run.id"), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("broker_exec_id", sa.String(128), nullable=False),
        sa.Column("broker_order_id", sa.String(64), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("side", sa.String(8), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("price", sa.Numeric(18, 6), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_broker_execution_sync", "broker_execution_snapshot", ["sync_run_id"])
    op.create_index(
        "ix_broker_execution_account_exec",
        "broker_execution_snapshot",
        ["account_id", "broker_exec_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_broker_execution_account_exec", table_name="broker_execution_snapshot")
    op.drop_index("ix_broker_execution_sync", table_name="broker_execution_snapshot")
    op.drop_table("broker_execution_snapshot")

    op.drop_index("ix_broker_open_order_account_order", table_name="broker_open_order_snapshot")
    op.drop_index("ix_broker_open_order_sync", table_name="broker_open_order_snapshot")
    op.drop_table("broker_open_order_snapshot")

    op.drop_index("ix_broker_position_account_symbol", table_name="broker_position_snapshot")
    op.drop_index("ix_broker_position_sync", table_name="broker_position_snapshot")
    op.drop_table("broker_position_snapshot")

    op.drop_index("ix_broker_cash_account_currency", table_name="broker_cash_snapshot")
    op.drop_index("ix_broker_cash_sync", table_name="broker_cash_snapshot")
    op.drop_table("broker_cash_snapshot")

    op.drop_index("ix_broker_account_sync", table_name="broker_account_snapshot")
    op.drop_table("broker_account_snapshot")

    op.drop_index("ix_broker_sync_run_status_started", table_name="broker_sync_run")
    op.drop_index("ix_broker_sync_run_started", table_name="broker_sync_run")
    op.drop_table("broker_sync_run")
