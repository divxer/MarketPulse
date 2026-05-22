"""Phase 6b+: extend paper_audit_event CHECK to allow PRICE_UNAVAILABLE.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-22

Lock 6b+L6: SQLite table rebuild (no ALTER CHECK).
Lock 6b+L10: column defs / defaults / index names match 0010 exactly.
Lock 6b+L13: INSERT-SELECT uses explicit column lists, never SELECT *.
"""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 12 event types from 0010 + the new 6b+ one.
_TYPES_6A = (
    "ORDER_PLACED", "ORDER_PLACED_DUPLICATE", "ORDER_REJECTED",
    "ORDER_CANCELLED", "ORDER_ENTRY_FILLED", "POSITION_CLOSED",
    "KILL_SWITCH_FLIPPED", "KILL_SWITCH_CYCLE_SKIPPED",
    "TICK_COMPLETED", "TICK_REPROCESSED_COMPLETED",
    "SCHEDULER_GAP_DETECTED", "ENGINE_INVARIANT_ERROR",
)
_TYPES_6B_PLUS = ("PRICE_UNAVAILABLE",)


def _check_clause(types: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{t}'" for t in types)
    return f"event_type IN ({joined})"


def _rebuild(new_check: str) -> None:
    """Rebuild paper_audit_event with the supplied CHECK clause.

    Lock 6b+L10: column definitions, defaults, and index names match 0010.
    """
    # 1. Create new table with same schema as 0010, replacing only the CHECK.
    op.execute(f"""
        CREATE TABLE paper_audit_event_new (
            id INTEGER NOT NULL,
            timestamp DATETIME NOT NULL,
            event_type VARCHAR(48) NOT NULL,
            order_id INTEGER,
            strategy VARCHAR(64),
            reason TEXT NOT NULL DEFAULT '',
            context JSON NOT NULL DEFAULT '{{}}',
            PRIMARY KEY (id),
            CONSTRAINT ck_paper_audit_event_type CHECK ({new_check})
        )
    """)
    # 2. Copy rows with explicit column list (lock 6b+L13).
    op.execute("""
        INSERT INTO paper_audit_event_new
            (id, timestamp, event_type, order_id, strategy, reason, context)
        SELECT id, timestamp, event_type, order_id, strategy, reason, context
        FROM paper_audit_event
    """)
    # 3. Drop old table.
    op.execute("DROP TABLE paper_audit_event")
    # 4. Rename new table.
    op.execute("ALTER TABLE paper_audit_event_new RENAME TO paper_audit_event")
    # 5. Recreate indexes with EXACT 0010 names (lock 6b+L10).
    op.execute("CREATE INDEX ix_paper_audit_ts ON paper_audit_event (timestamp)")
    op.execute(
        "CREATE INDEX ix_paper_audit_type_ts ON paper_audit_event "
        "(event_type, timestamp)"
    )
    op.execute("CREATE INDEX ix_paper_audit_order ON paper_audit_event (order_id)")
    op.execute(
        "CREATE INDEX ix_paper_audit_strategy_ts ON paper_audit_event "
        "(strategy, timestamp)"
    )


def upgrade() -> None:
    _rebuild(_check_clause(_TYPES_6A + _TYPES_6B_PLUS))


def downgrade() -> None:
    """Refuse to downgrade if PRICE_UNAVAILABLE rows exist (would orphan
    them under old CHECK). Lock 6b+L10."""
    conn = op.get_bind()
    count = conn.execute(text(
        "SELECT COUNT(*) FROM paper_audit_event "
        "WHERE event_type = 'PRICE_UNAVAILABLE'"
    )).scalar() or 0
    if count > 0:
        raise RuntimeError(
            f"Cannot downgrade 0011 → 0010: {count} PRICE_UNAVAILABLE row(s) "
            "would violate the 0010 CHECK constraint. Delete them first or "
            "implement a manual data-loss-acceptable rollback."
        )
    _rebuild(_check_clause(_TYPES_6A))
