# Layer: stateful
"""PR3a — paper_nav_snapshot migration tests.

Mirrors the existing migration-test pattern (see test_0013_*): env var →
settings → alembic subprocess. No env.py modification required.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime

import sqlalchemy as sa


def _upgrade(tmp_path, monkeypatch, *, target: str = "head") -> str:
    db_file = tmp_path / "pr3a.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", target], check=True,
    )
    return db_url


def _downgrade(tmp_path, monkeypatch, *, target: str) -> str:
    db_url = f"sqlite:///{tmp_path/'pr3a.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    subprocess.run(
        ["uv", "run", "alembic", "downgrade", target], check=True,
    )
    return db_url


def test_alembic_upgrade_creates_table(tmp_path, monkeypatch):
    db_url = _upgrade(tmp_path, monkeypatch, target="0014")
    engine = sa.create_engine(db_url)
    insp = sa.inspect(engine)
    assert "paper_nav_snapshot" in insp.get_table_names()

    cols = {c["name"] for c in insp.get_columns("paper_nav_snapshot")}
    expected = {
        "trading_date", "cash_balance", "holdings_mtm", "portfolio_nav",
        "anchor_portfolio_nav", "portfolio_index", "spy_close",
        "anchor_spy_close", "spy_index", "excess_return",
        "trading_days_observed", "coverage_ratio", "is_sufficient",
        "unpriced_positions_count", "unpriced_tickers",
        "created_at", "updated_at", "is_rebuilt", "rebuild_reason",
    }
    assert cols == expected


def test_alembic_downgrade_drops_table(tmp_path, monkeypatch):
    db_url = _upgrade(tmp_path, monkeypatch, target="0014")
    _downgrade(tmp_path, monkeypatch, target="0013")
    engine = sa.create_engine(db_url)
    insp = sa.inspect(engine)
    assert "paper_nav_snapshot" not in insp.get_table_names()


def test_column_defaults_safe_for_hand_insert(tmp_path, monkeypatch):
    """is_rebuilt defaults to 0, unpriced_positions_count defaults to 0."""
    db_url = _upgrade(tmp_path, monkeypatch, target="0014")
    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        conn.execute(sa.text("""
            INSERT INTO paper_nav_snapshot (
                trading_date, cash_balance, holdings_mtm, portfolio_nav,
                anchor_portfolio_nav, portfolio_index,
                trading_days_observed, coverage_ratio, is_sufficient,
                created_at, updated_at
            ) VALUES (
                '2026-05-28', 100000, 0, 100000, 100000, 1,
                1, 0.011, 0,
                :created, :updated
            )
        """), {
            "created": datetime(2026, 5, 28, tzinfo=UTC).isoformat(),
            "updated": datetime(2026, 5, 28, tzinfo=UTC).isoformat(),
        })
        conn.commit()
        row = conn.execute(sa.text(
            "SELECT is_rebuilt, unpriced_positions_count, unpriced_tickers "
            "FROM paper_nav_snapshot WHERE trading_date='2026-05-28'"
        )).first()
    assert row.is_rebuilt == 0
    assert row.unpriced_positions_count == 0
    assert row.unpriced_tickers is None
