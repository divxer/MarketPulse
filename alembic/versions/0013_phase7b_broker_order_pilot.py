"""Phase 7b broker order intent + event tables.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-24
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Spec L65: 14 event_type values (exact order)
_EVENT_TYPES = (
    "safety_rejected",
    "connection_failed",
    "account_mismatch",
    "next_valid_id_received",
    "staged_to_tws",
    "submitted_to_broker",
    "open_order_seen",
    "order_status_seen",
    "broker_cancel_requested",
    "staged_cancelled",
    "cancelled",
    "filled",
    "rejected",
    "error",
)

# Spec L75: intent status enum (exactly 5)
_INTENT_STATUSES = ("created", "sent", "completed", "rejected", "failed")

_EVENT_SOURCES = ("adapter_callback", "service_safety", "cli_validation", "timeout")


def _in_clause(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_table(
        "broker_order_intent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("operator_source", sa.String(16), nullable=False, server_default="cli"),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("broker", sa.String(16), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("asset_class", sa.String(8), nullable=True),
        sa.Column("side", sa.String(4), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("order_type", sa.String(8), nullable=True),
        sa.Column("limit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("transmit", sa.Boolean(), nullable=True),
        sa.Column("local_idempotency_key", sa.String(64), nullable=False),
        sa.Column(
            "parent_intent_id",
            sa.Integer(),
            sa.ForeignKey("broker_order_intent.id"),
            nullable=True,
        ),
        sa.Column("broker_order_id", sa.String(32), nullable=True),
        sa.Column("broker_perm_id", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="created"),
        sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "action IN ('place', 'cancel', 'status_check')",
            name="ck_broker_order_intent_action",
        ),
        sa.CheckConstraint("broker IN ('IBKR')", name="ck_broker_order_intent_broker"),
        sa.CheckConstraint(
            "broker_environment IN ('paper', 'live', 'unknown')",
            name="ck_broker_order_intent_environment",
        ),
        sa.CheckConstraint(
            "asset_class IS NULL OR asset_class IN ('STK')",
            name="ck_broker_order_intent_asset_class",
        ),
        sa.CheckConstraint(
            "side IS NULL OR side IN ('BUY', 'SELL')",
            name="ck_broker_order_intent_side",
        ),
        sa.CheckConstraint(
            "order_type IS NULL OR order_type IN ('LMT')",
            name="ck_broker_order_intent_order_type",
        ),
        sa.CheckConstraint(
            f"status IN ({_in_clause(_INTENT_STATUSES)})",
            name="ck_broker_order_intent_status",
        ),
        sa.UniqueConstraint(
            "account_id",
            "action",
            "local_idempotency_key",
            name="uq_broker_order_intent_idem",
        ),
    )
    op.create_index(
        "ix_broker_order_intent_created",
        "broker_order_intent",
        ["created_at"],
    )
    op.create_index(
        "ix_broker_order_intent_account_action_created",
        "broker_order_intent",
        ["account_id", "action", "created_at"],
    )
    op.create_index(
        "ix_broker_order_intent_parent",
        "broker_order_intent",
        ["parent_intent_id"],
    )

    op.create_table(
        "broker_order_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "intent_id",
            sa.Integer(),
            sa.ForeignKey("broker_order_intent.id"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("event_source", sa.String(16), nullable=False),
        sa.Column("broker_order_id", sa.String(32), nullable=True),
        sa.Column("broker_perm_id", sa.String(32), nullable=True),
        sa.Column("broker_status", sa.String(32), nullable=True),
        sa.Column("filled_quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("remaining_quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("avg_fill_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            f"event_type IN ({_in_clause(_EVENT_TYPES)})",
            name="ck_broker_order_event_type",
        ),
        sa.CheckConstraint(
            f"event_source IN ({_in_clause(_EVENT_SOURCES)})",
            name="ck_broker_order_event_source",
        ),
    )
    op.create_index(
        "ix_broker_order_event_intent",
        "broker_order_event",
        ["intent_id"],
    )
    op.create_index(
        "ix_broker_order_event_observed",
        "broker_order_event",
        ["observed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_broker_order_event_observed", table_name="broker_order_event")
    op.drop_index("ix_broker_order_event_intent", table_name="broker_order_event")
    op.drop_table("broker_order_event")

    op.drop_index("ix_broker_order_intent_parent", table_name="broker_order_intent")
    op.drop_index(
        "ix_broker_order_intent_account_action_created",
        table_name="broker_order_intent",
    )
    op.drop_index("ix_broker_order_intent_created", table_name="broker_order_intent")
    op.drop_table("broker_order_intent")
