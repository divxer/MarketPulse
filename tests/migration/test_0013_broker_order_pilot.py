# Layer: stateful
"""Phase 7b Task 2: Alembic 0013 broker_order_intent + broker_order_event tables."""

import subprocess
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

NEW_TABLES = {"broker_order_intent", "broker_order_event"}

EVENT_TYPES = [
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
]

EVENT_SOURCES = ["adapter_callback", "service_safety", "cli_validation", "timeout"]
INTENT_STATUSES = ["created", "sent", "completed", "rejected", "failed"]


def _upgrade(tmp_path, monkeypatch) -> str:
    db_file = tmp_path / "phase7b.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    from marketpulse.config import get_settings

    get_settings.cache_clear()
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)
    return db_url


def _insert_intent(conn, **overrides) -> int:
    row = {
        "created_at": datetime.now(UTC),
        "operator_source": "cli",
        "action": "place",
        "broker": "IBKR",
        "broker_environment": "paper",
        "account_id": "DU123",
        "symbol": "AAPL",
        "asset_class": "STK",
        "side": "BUY",
        "quantity": "10",
        "order_type": "LMT",
        "limit_price": "150.00",
        "transmit": False,
        "local_idempotency_key": "idem-key-1",
        "status": "created",
        "context": "{}",
    }
    row.update(overrides)
    cols = ", ".join(row.keys())
    placeholders = ", ".join(f":{k}" for k in row)
    result = conn.execute(
        text(f"INSERT INTO broker_order_intent ({cols}) VALUES ({placeholders})"),
        row,
    )
    return result.lastrowid


def test_0013_upgrade_creates_tables_with_expected_columns(tmp_path, monkeypatch):
    db_url = _upgrade(tmp_path, monkeypatch)
    engine = create_engine(db_url)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert tables >= NEW_TABLES

    intent_cols = {c["name"] for c in insp.get_columns("broker_order_intent")}
    assert intent_cols >= {
        "id",
        "created_at",
        "operator_source",
        "action",
        "broker",
        "broker_environment",
        "account_id",
        "symbol",
        "asset_class",
        "side",
        "quantity",
        "order_type",
        "limit_price",
        "transmit",
        "local_idempotency_key",
        "parent_intent_id",
        "broker_order_id",
        "broker_perm_id",
        "status",
        "context",
    }

    event_cols = {c["name"] for c in insp.get_columns("broker_order_event")}
    assert event_cols >= {
        "id",
        "intent_id",
        "observed_at",
        "event_type",
        "event_source",
        "broker_order_id",
        "broker_perm_id",
        "broker_status",
        "filled_quantity",
        "remaining_quantity",
        "avg_fill_price",
        "message",
        "raw",
    }

    # FK from event.intent_id → intent.id
    fks = insp.get_foreign_keys("broker_order_event")
    assert any(
        fk["referred_table"] == "broker_order_intent" and fk["constrained_columns"] == ["intent_id"]
        for fk in fks
    )


def test_0013_intent_status_check_rejects_unknown(tmp_path, monkeypatch):
    db_url = _upgrade(tmp_path, monkeypatch)
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
        # Valid statuses succeed
        for i, status in enumerate(INTENT_STATUSES):
            _insert_intent(
                conn,
                status=status,
                local_idempotency_key=f"ok-{i}",
            )
        # Unknown status rejected
        with pytest.raises(IntegrityError):
            _insert_intent(
                conn,
                status="not_a_status",
                local_idempotency_key="bad",
            )


def test_0013_event_type_check_rejects_unknown(tmp_path, monkeypatch):
    db_url = _upgrade(tmp_path, monkeypatch)
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
        intent_id = _insert_intent(conn)
        # All 14 spec values valid
        for _i, etype in enumerate(EVENT_TYPES):
            conn.execute(
                text(
                    "INSERT INTO broker_order_event "
                    "(intent_id, observed_at, event_type, event_source, raw) "
                    "VALUES (:i, :o, :t, :s, '{}')"
                ),
                {"i": intent_id, "o": datetime.now(UTC), "t": etype, "s": "adapter_callback"},
            )
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO broker_order_event "
                    "(intent_id, observed_at, event_type, event_source, raw) "
                    "VALUES (:i, :o, 'bogus', 'adapter_callback', '{}')"
                ),
                {"i": intent_id, "o": datetime.now(UTC)},
            )


def test_0013_event_source_check_rejects_unknown(tmp_path, monkeypatch):
    db_url = _upgrade(tmp_path, monkeypatch)
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
        intent_id = _insert_intent(conn)
        for src in EVENT_SOURCES:
            conn.execute(
                text(
                    "INSERT INTO broker_order_event "
                    "(intent_id, observed_at, event_type, event_source, raw) "
                    "VALUES (:i, :o, 'staged_to_tws', :s, '{}')"
                ),
                {"i": intent_id, "o": datetime.now(UTC), "s": src},
            )
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO broker_order_event "
                    "(intent_id, observed_at, event_type, event_source, raw) "
                    "VALUES (:i, :o, 'staged_to_tws', 'nope', '{}')"
                ),
                {"i": intent_id, "o": datetime.now(UTC)},
            )


def test_0013_unique_idempotency_account_action_key(tmp_path, monkeypatch):
    db_url = _upgrade(tmp_path, monkeypatch)
    engine = create_engine(db_url)
    with engine.begin() as conn:
        _insert_intent(
            conn,
            account_id="DU111",
            action="place",
            local_idempotency_key="dup-key",
        )
        # Different account → OK
        _insert_intent(
            conn,
            account_id="DU222",
            action="place",
            local_idempotency_key="dup-key",
        )
        # Different action → OK
        _insert_intent(
            conn,
            account_id="DU111",
            action="cancel",
            local_idempotency_key="dup-key",
        )
        # Same triple → reject
        with pytest.raises(IntegrityError):
            _insert_intent(
                conn,
                account_id="DU111",
                action="place",
                local_idempotency_key="dup-key",
            )


def test_0013_downgrade_drops_tables_cleanly(tmp_path, monkeypatch):
    db_url = _upgrade(tmp_path, monkeypatch)
    subprocess.run(["uv", "run", "alembic", "downgrade", "0012"], check=True)

    engine = create_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert NEW_TABLES.isdisjoint(tables)
    # 7a tables remain
    assert "broker_sync_run" in tables
