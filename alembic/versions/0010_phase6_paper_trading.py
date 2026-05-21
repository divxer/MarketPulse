"""phase6 paper trading tables

Revision ID: 0010
Revises: 0009_aianalyses_strategy
Create Date: 2026-05-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009_aianalyses_strategy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # paper_audit_event — no FKs (root of the tree)
    op.create_table(
        "paper_audit_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("strategy", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "event_type IN ("
            "'ORDER_PLACED', 'ORDER_PLACED_DUPLICATE', 'ORDER_REJECTED', "
            "'ORDER_CANCELLED', 'ORDER_ENTRY_FILLED', 'POSITION_CLOSED', "
            "'KILL_SWITCH_FLIPPED', 'KILL_SWITCH_CYCLE_SKIPPED', "
            "'TICK_COMPLETED', 'TICK_REPROCESSED_COMPLETED', "
            "'SCHEDULER_GAP_DETECTED', 'ENGINE_INVARIANT_ERROR'"
            ")",
            name="ck_paper_audit_event_type",
        ),
    )
    op.create_index("ix_paper_audit_ts", "paper_audit_event", ["timestamp"])
    op.create_index("ix_paper_audit_type_ts", "paper_audit_event", ["event_type", "timestamp"])
    op.create_index("ix_paper_audit_order", "paper_audit_event", ["order_id"])
    op.create_index("ix_paper_audit_strategy_ts", "paper_audit_event", ["strategy", "timestamp"])

    # paper_order
    op.create_table(
        "paper_order",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(32), nullable=False, unique=True),
        sa.Column("allocation_run_id", sa.String(64), nullable=False),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("allocation_date", sa.Date(), nullable=False),
        sa.Column("horizon_date", sa.Date(), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("event_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("horizon_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("strategy_version", sa.String(32), nullable=False),
        sa.Column("allocator_version", sa.String(32), nullable=False),
        sa.Column("execution_engine_version", sa.String(32), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("raw_bid_weight", sa.Float(), nullable=True),
        sa.Column("pool_corr", sa.Float(), nullable=True),
        sa.Column("contribution_multiplier", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("adjusted_bid_weight", sa.Float(), nullable=True),
        sa.Column("effective_corr_window", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("rewarded_for_negative_corr", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("would_change_rank", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("size_clamped_by_override", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("status IN ('PLACED', 'ENTRY_FILLED', 'CANCELLED')", name="ck_paper_order_status"),
        sa.CheckConstraint("quantity > 0", name="ck_paper_order_qty_positive"),
        # Time-consistency CHECKs (spec § 4.1):
        sa.CheckConstraint(
            "status != 'PLACED' OR (filled_at IS NULL AND cancelled_at IS NULL)",
            name="ck_paper_order_placed_no_terminal_ts",
        ),
        sa.CheckConstraint(
            "status != 'ENTRY_FILLED' OR filled_at IS NOT NULL",
            name="ck_paper_order_entry_filled_has_ts",
        ),
        sa.CheckConstraint(
            "status != 'CANCELLED' OR cancelled_at IS NOT NULL",
            name="ck_paper_order_cancelled_has_ts",
        ),
    )
    op.create_index("ix_paper_order_status_horizon", "paper_order", ["status", "horizon_date"])
    op.create_index("ix_paper_order_status_alloc_date", "paper_order", ["status", "allocation_date"])
    op.create_index("ix_paper_order_alloc_date_strategy", "paper_order", ["allocation_date", "strategy"])
    op.create_index("ix_paper_order_strategy_placed", "paper_order", ["strategy", "placed_at"])
    op.create_index("ix_paper_order_run_id", "paper_order", ["allocation_run_id"])

    # paper_position (no FK to paper_fill — see spec § 4.7)
    op.create_table(
        "paper_position",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("paper_order.id"), nullable=False, unique=True),
        sa.Column("entry_fill_id", sa.Integer(), nullable=True),
        sa.Column("exit_fill_id", sa.Integer(), nullable=True),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("horizon_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(18, 6), nullable=True),
        sa.CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_paper_position_status"),
        sa.CheckConstraint("status != 'OPEN' OR exit_fill_id IS NULL", name="ck_paper_position_open_no_exit"),
        sa.CheckConstraint(
            "status != 'CLOSED' OR (entry_fill_id IS NOT NULL AND exit_fill_id IS NOT NULL)",
            name="ck_paper_position_closed_both_set",
        ),
    )
    op.create_index("ix_paper_position_status_horizon", "paper_position", ["status", "horizon_date"])
    op.create_index("ix_paper_position_strategy_ticker", "paper_position", ["strategy", "ticker"])
    op.create_index("ix_paper_position_entry_fill", "paper_position", ["entry_fill_id"])
    op.create_index("ix_paper_position_exit_fill", "paper_position", ["exit_fill_id"])

    # paper_fill
    op.create_table(
        "paper_fill",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("paper_order.id"), nullable=False),
        sa.Column(
            "position_id", sa.Integer(),
            sa.ForeignKey("paper_position.id"), nullable=False,
        ),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cash_delta", sa.Numeric(18, 6), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(18, 6), nullable=True),
        sa.UniqueConstraint("order_id", "side", name="uq_paper_fill_order_side"),
        sa.CheckConstraint("side IN ('ENTRY', 'EXIT')", name="ck_paper_fill_side"),
        sa.CheckConstraint("quantity > 0", name="ck_paper_fill_qty_positive"),
    )
    op.create_index("ix_paper_fill_order_id", "paper_fill", ["order_id"])
    op.create_index("ix_paper_fill_position_side", "paper_fill", ["position_id", "side"])

    # paper_cash_ledger
    op.create_table(
        "paper_cash_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delta", sa.Numeric(18, 6), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("fill_id", sa.Integer(), sa.ForeignKey("paper_fill.id"), nullable=True),
        sa.Column("balance_after", sa.Numeric(18, 6), nullable=False),
        sa.CheckConstraint(
            "reason IN ('ENTRY_FILL', 'EXIT_FILL', 'INITIAL_DEPOSIT', 'MANUAL_ADJUSTMENT')",
            name="ck_paper_cash_reason",
        ),
    )
    op.create_index("ix_paper_cash_ts", "paper_cash_ledger", ["timestamp"])
    op.create_index("ix_paper_cash_fill", "paper_cash_ledger", ["fill_id"])


def downgrade() -> None:
    op.drop_table("paper_cash_ledger")
    op.drop_table("paper_fill")
    op.drop_table("paper_position")
    op.drop_table("paper_order")
    op.drop_table("paper_audit_event")
