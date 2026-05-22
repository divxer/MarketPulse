# Layer: stateful
"""6b+T2: Alembic 0011 — extend paper_audit_event CHECK to include
PRICE_UNAVAILABLE. Lock 6b+L6 (SQLite table rebuild), 6b+L10 (schema
preservation), 6b+L13 (explicit column INSERT)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.command import downgrade as alembic_downgrade
from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    # NOTE: alembic/env.py overrides sqlalchemy.url with get_settings().database_url
    # (line 15), so we MUST set DATABASE_URL env + clear lru_cache before any
    # alembic command runs. Setting cfg.set_main_option alone is insufficient.
    from marketpulse.config import get_settings

    db_url = f"sqlite:///{tmp_path / 'mig.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.db_url = db_url
    yield cfg
    get_settings.cache_clear()


def _engine(cfg):
    return create_engine(cfg.db_url)


def test_0011_upgrade_inserts_price_unavailable_succeeds(alembic_cfg):
    """After 0011 upgrade, INSERT of PRICE_UNAVAILABLE passes the CHECK."""
    alembic_upgrade(alembic_cfg, "0011")
    eng = _engine(alembic_cfg)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO paper_audit_event "
            "(timestamp, event_type, order_id, strategy, reason, context) "
            "VALUES (:ts, 'PRICE_UNAVAILABLE', 1, 'test', 'no_data', '{}')"
        ), {"ts": datetime.now(UTC)})
        row = conn.execute(text(
            "SELECT event_type FROM paper_audit_event WHERE event_type='PRICE_UNAVAILABLE'"
        )).fetchone()
        assert row is not None
        assert row[0] == "PRICE_UNAVAILABLE"


def test_0011_upgrade_preserves_6a_indexes_exact_names(alembic_cfg):
    """Lock 6b+L10: all 4 indexes match 0010 names EXACTLY."""
    alembic_upgrade(alembic_cfg, "0011")
    eng = _engine(alembic_cfg)
    with eng.begin() as conn:
        rows = conn.execute(text(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='paper_audit_event' "
            "AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )).fetchall()
        names = {r[0] for r in rows}
        assert names == {
            "ix_paper_audit_ts",
            "ix_paper_audit_type_ts",
            "ix_paper_audit_order",
            "ix_paper_audit_strategy_ts",
        }


def test_0011_upgrade_preserves_6a_rows(alembic_cfg):
    """Op-test #24: seed 6a rows, upgrade, assert all preserved."""
    # First upgrade only to 0010
    alembic_upgrade(alembic_cfg, "0010")
    eng = _engine(alembic_cfg)
    ts = datetime.now(UTC)
    with eng.begin() as conn:
        for et in ("ORDER_PLACED", "POSITION_CLOSED", "TICK_COMPLETED",
                   "KILL_SWITCH_FLIPPED", "ENGINE_INVARIANT_ERROR"):
            conn.execute(text(
                "INSERT INTO paper_audit_event "
                "(timestamp, event_type, order_id, strategy, reason, context) "
                "VALUES (:ts, :et, 1, 'test', 'seed', '{}')"
            ), {"ts": ts, "et": et})

    # Now upgrade to 0011 (which rebuilds the table)
    alembic_upgrade(alembic_cfg, "0011")

    with eng.begin() as conn:
        rows = conn.execute(text(
            "SELECT event_type FROM paper_audit_event ORDER BY event_type"
        )).fetchall()
        assert {r[0] for r in rows} == {
            "ORDER_PLACED", "POSITION_CLOSED", "TICK_COMPLETED",
            "KILL_SWITCH_FLIPPED", "ENGINE_INVARIANT_ERROR",
        }


def test_0011_downgrade_with_no_price_unavailable_succeeds(alembic_cfg):
    """Lock 6b+L10 downgrade: with 0 PRICE_UNAVAILABLE rows, downgrade
    succeeds and rebuilds the old CHECK."""
    alembic_upgrade(alembic_cfg, "0011")
    eng = _engine(alembic_cfg)
    ts = datetime.now(UTC)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO paper_audit_event "
            "(timestamp, event_type, order_id, strategy, reason, context) "
            "VALUES (:ts, 'ORDER_PLACED', 1, 'test', '', '{}')"
        ), {"ts": ts})

    alembic_downgrade(alembic_cfg, "0010")

    # Old CHECK should reject PRICE_UNAVAILABLE now
    with eng.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(text(
            "INSERT INTO paper_audit_event "
            "(timestamp, event_type, order_id, strategy, reason, context) "
            "VALUES (:ts, 'PRICE_UNAVAILABLE', 1, 'test', '', '{}')"
        ), {"ts": ts})


def test_0011_downgrade_with_price_unavailable_rows_raises(alembic_cfg):
    """Lock 6b+L10: refuses to downgrade if PRICE_UNAVAILABLE rows exist."""
    alembic_upgrade(alembic_cfg, "0011")
    eng = _engine(alembic_cfg)
    ts = datetime.now(UTC)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO paper_audit_event "
            "(timestamp, event_type, order_id, strategy, reason, context) "
            "VALUES (:ts, 'PRICE_UNAVAILABLE', 1, 'test', 'no_data', '{}')"
        ), {"ts": ts})

    with pytest.raises(RuntimeError, match="PRICE_UNAVAILABLE"):
        alembic_downgrade(alembic_cfg, "0010")
