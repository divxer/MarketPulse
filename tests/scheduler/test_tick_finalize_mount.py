# Layer: scheduler
"""Finalize is the structural step BEFORE the NAV snapshot (spec §4 mount)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketpulse.db.base import Base


class _StubNotifier:
    def send(self, title: str, body: str, url: str | None = None) -> bool:
        return True


@pytest.fixture
def tick_harness(tmp_path, monkeypatch):
    """Minimal tick invocation harness, mirrored from
    tests/trading/test_paper_tick_notifies_after_run.py::patched_scheduler.
    """
    from marketpulse.config import get_settings
    from marketpulse.scheduler import paper_trading_tick as module
    from marketpulse.trading.clock import FakeClock

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    get_settings.cache_clear()

    engine = create_engine(f"sqlite:///{tmp_path / 'pt.db'}")
    Base.metadata.create_all(engine)

    def scope():
        with Session(engine) as session:
            yield session

    fixed_now = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    monkeypatch.setattr(module, "session_scope", scope)
    monkeypatch.setattr(module, "WallClock", lambda: FakeClock(now=fixed_now))

    def fake_run(**kwargs):
        return SimpleNamespace(
            tick_date=date(2026, 5, 22),
            orders_placed=0,
            exits_materialized=0,
            entries_materialized=0,
            tick_errors=(),
        )

    monkeypatch.setattr(module.daily_cycle, "run", fake_run)
    monkeypatch.setattr(module, "notify_paper_tick_events", lambda **kwargs: None)
    yield module
    get_settings.cache_clear()


def test_finalize_runs_before_nav_snapshot(tick_harness, monkeypatch):
    """# Layer: behavioral — finalize is step 0 of the NAV snapshot."""
    module = tick_harness
    calls: list[str] = []
    monkeypatch.setattr(
        "marketpulse.scheduler.paper_trading_tick.finalize_provisional_bars",
        lambda session: calls.append("finalize"),
    )
    monkeypatch.setattr(
        "marketpulse.scheduler.paper_trading_tick._run_nav_snapshot_safely",
        lambda session, *, tick_date: calls.append("nav"),
    )

    module.paper_trading_tick_job(notifier=_StubNotifier())

    assert calls == ["finalize", "nav"]


def test_finalize_failure_does_not_abort_tick(tick_harness, monkeypatch):
    """# Layer: behavioral — a finalize crash must never abort the tick."""
    module = tick_harness

    def boom(session):
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(
        "marketpulse.scheduler.paper_trading_tick.finalize_provisional_bars", boom,
    )
    seen: list[date] = []
    monkeypatch.setattr(
        "marketpulse.scheduler.paper_trading_tick._run_nav_snapshot_safely",
        lambda session, *, tick_date: seen.append(tick_date),
    )

    module.paper_trading_tick_job(notifier=_StubNotifier())

    assert seen  # NAV still ran
