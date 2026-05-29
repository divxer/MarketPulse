# Layer: test
"""PR3a — paper_nav_snapshot migration tests."""
from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command


def _alembic_config(db_url: str) -> Config:
    ini = Path(__file__).resolve().parent.parent.parent / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _table_columns(engine, table: str) -> dict[str, str]:
    insp = sa.inspect(engine)
    return {c["name"]: str(c["type"]) for c in insp.get_columns(table)}


def test_alembic_upgrade_creates_table(tmp_path):
    db_url = f"sqlite:///{tmp_path/'mig.db'}"
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "0014")

    engine = sa.create_engine(db_url)
    insp = sa.inspect(engine)
    assert "paper_nav_snapshot" in insp.get_table_names()

    cols = _table_columns(engine, "paper_nav_snapshot")
    expected = {
        "trading_date", "cash_balance", "holdings_mtm", "portfolio_nav",
        "anchor_portfolio_nav", "portfolio_index", "spy_close",
        "anchor_spy_close", "spy_index", "excess_return",
        "trading_days_observed", "coverage_ratio", "is_sufficient",
        "unpriced_positions_count", "unpriced_tickers",
        "created_at", "updated_at", "is_rebuilt", "rebuild_reason",
    }
    assert set(cols.keys()) == expected


def test_alembic_downgrade_drops_table(tmp_path):
    db_url = f"sqlite:///{tmp_path/'mig.db'}"
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "0014")
    command.downgrade(cfg, "0013")

    engine = sa.create_engine(db_url)
    insp = sa.inspect(engine)
    assert "paper_nav_snapshot" not in insp.get_table_names()


def test_column_defaults_safe_for_hand_insert(tmp_path):
    """is_rebuilt defaults to 0, unpriced_positions_count defaults to 0."""
    db_url = f"sqlite:///{tmp_path/'mig.db'}"
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "0014")
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
                '2026-05-28T00:00:00+00:00', '2026-05-28T00:00:00+00:00'
            )
        """))
        conn.commit()
        row = conn.execute(sa.text(
            "SELECT is_rebuilt, unpriced_positions_count, unpriced_tickers "
            "FROM paper_nav_snapshot WHERE trading_date='2026-05-28'"
        )).first()
    assert row.is_rebuilt == 0
    assert row.unpriced_positions_count == 0
    assert row.unpriced_tickers is None
