# Layer: behavioral
"""6a-3.3: ensure_initial_deposit idempotency + settings defaults."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from marketpulse.db.base import Base
from marketpulse.db.models import PaperCashLedger
from marketpulse.trading.repository import Repository


def test_ensure_initial_deposit_idempotent(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        repo = Repository(session=s)
        ts = datetime(2026, 5, 21, tzinfo=UTC)
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=ts)
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=ts)
        rows = s.execute(select(PaperCashLedger)).scalars().all()
        assert len(rows) == 1
        assert rows[0].balance_after == Decimal("10000")
        assert rows[0].reason == "INITIAL_DEPOSIT"


def test_paper_settings_defaults():
    """The four paper_* settings are exposed with documented defaults."""
    from marketpulse.config import Settings

    # Build a Settings using ONLY env defaults — pass through min required
    # secrets. Pydantic BaseSettings requires the (...) fields to come
    # from somewhere; supply them explicitly so the test is hermetic.
    s = Settings(
        APP_PASSWORD_HASH="x",
        SESSION_SECRET="0123456789abcdef",
        ANTHROPIC_API_KEY="x",
    )
    assert s.paper_tick_hour == 17
    assert s.paper_tick_minute == 30
    assert s.paper_initial_deposit == "10000"
    assert s.paper_kill_switch is False
