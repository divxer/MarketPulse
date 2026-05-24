# Layer: stateful
"""Phase 7a Task 2: Alembic 0012 broker snapshot tables."""

import subprocess

from sqlalchemy import create_engine, inspect

BROKER_TABLES = {
    "broker_sync_run",
    "broker_account_snapshot",
    "broker_cash_snapshot",
    "broker_position_snapshot",
    "broker_open_order_snapshot",
    "broker_execution_snapshot",
}


def test_0012_upgrade_creates_broker_snapshot_tables(tmp_path, monkeypatch):
    db_file = tmp_path / "broker_snapshots.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    from marketpulse.config import get_settings

    get_settings.cache_clear()

    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)

    engine = create_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert tables >= BROKER_TABLES


def test_0012_downgrade_drops_broker_snapshot_tables_only(tmp_path, monkeypatch):
    db_file = tmp_path / "broker_snapshots.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    from marketpulse.config import get_settings

    get_settings.cache_clear()

    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)
    subprocess.run(["uv", "run", "alembic", "downgrade", "0011"], check=True)

    engine = create_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert BROKER_TABLES.isdisjoint(tables)
    assert "paper_order" in tables
