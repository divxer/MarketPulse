"""Phase 6h ops hardening script tests."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_ops_common_resolves_db_url_from_arg_or_env(monkeypatch):
    from scripts._paper_ops_common import resolve_db_url

    monkeypatch.delenv("MARKETPULSE_DB_URL", raising=False)
    assert resolve_db_url("sqlite:///explicit.db") == "sqlite:///explicit.db"
    assert resolve_db_url(None) == "sqlite:///./data/marketpulse.db"

    monkeypatch.setenv("MARKETPULSE_DB_URL", "sqlite:///env.db")
    assert resolve_db_url(None) == "sqlite:///env.db"


def test_ops_common_counts_paper_tables(tmp_path):
    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperCashLedger
    from scripts._paper_ops_common import count_paper_tables

    engine = create_engine(f"sqlite:///{tmp_path / 'ops.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            PaperCashLedger(
                timestamp=datetime(2026, 5, 23, tzinfo=UTC),
                delta=100,
                reason="INITIAL_DEPOSIT",
                balance_after=100,
            ),
        )
        session.commit()

        counts = count_paper_tables(session)

    assert counts["paper_order"] == 0
    assert counts["paper_fill"] == 0
    assert counts["paper_position"] == 0
    assert counts["paper_cash_ledger"] == 1
    assert counts["paper_audit_event"] == 0
