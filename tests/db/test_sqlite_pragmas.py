# Layer: test
"""SQLite WAL + busy_timeout are applied on every connection (lock hardening)."""
from __future__ import annotations

from sqlalchemy import text

from marketpulse.db import base as db_base


def test_sqlite_wal_and_busy_timeout(tmp_path):
    url = f"sqlite:///{tmp_path / 'pragma.db'}"
    db_base.init_engine(url)
    try:
        eng = db_base.get_engine()
        with eng.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000
    finally:
        db_base.reset_engine()
