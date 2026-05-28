# Layer: stateful
"""6g-T7: scheduler job invokes paper notification dispatch after the tick."""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketpulse.db.base import Base


class CapturingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []

    def send(self, title: str, body: str, url: str | None = None) -> bool:
        self.sent.append((title, body, url))
        return True


@pytest.fixture
def patched_scheduler(tmp_path, monkeypatch):
    from marketpulse.config import get_settings
    from marketpulse.scheduler import paper_trading_tick as module
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.types import AuditEventType

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    get_settings.cache_clear()

    engine = create_engine(f"sqlite:///{tmp_path / 'pt.db'}")
    Base.metadata.create_all(engine)

    def scope():
        with Session(engine) as session:
            yield session

    fixed_now = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    monkeypatch.setattr(module, "session_scope", scope)
    monkeypatch.setattr(module, "WallClock", lambda: FakeClock(now=fixed_now))

    call_order: list[str] = []

    def fake_run(**kwargs):
        call_order.append("run")
        repository = kwargs["repository"]
        clock = kwargs["clock"]
        with repository.transaction():
            repository.write_audit_event(
                event_type=AuditEventType.TICK_COMPLETED,
                order_id=None,
                strategy=None,
                reason="",
                context={"tick_date": "2026-05-22", "status": "completed"},
                timestamp=clock.now(),
            )
        return SimpleNamespace(
            tick_date=date(2026, 5, 22),
            orders_placed=0,
            exits_materialized=0,
            entries_materialized=0,
            tick_errors=(),
        )

    monkeypatch.setattr(module.daily_cycle, "run", fake_run)
    yield module, call_order
    get_settings.cache_clear()


def test_paper_trading_tick_job_calls_notifier_with_heartbeat(patched_scheduler):
    module, call_order = patched_scheduler
    notifier = CapturingNotifier()

    module.paper_trading_tick_job(notifier=notifier)

    assert call_order == ["run"]
    assert any(
        title.startswith("📊 Paper Tick")
        for title, _, _ in notifier.sent
    ), f"expected heartbeat summary push, got {notifier.sent!r}"


def test_paper_trading_tick_job_default_notifier_is_settings_driven(
    patched_scheduler,
    monkeypatch,
):
    module, _ = patched_scheduler
    monkeypatch.setenv("NOTIFIER_KIND", "none")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    module.paper_trading_tick_job()


def test_paper_trading_tick_job_passes_safe_sector_to_daily_cycle(
    patched_scheduler,
    monkeypatch,
):
    module, _ = patched_scheduler
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            tick_date=date(2026, 5, 22),
            orders_placed=0,
            exits_materialized=0,
            entries_materialized=0,
            tick_errors=(),
        )

    monkeypatch.setattr(module.daily_cycle, "run", fake_run)
    monkeypatch.setattr(module, "notify_paper_tick_events", lambda **kwargs: None)

    module.paper_trading_tick_job(notifier=CapturingNotifier())

    assert captured["sector_provider"] is module.rg.safe_sector


def test_paper_trading_tick_job_signature_compatible_with_apscheduler():
    from marketpulse.scheduler.paper_trading_tick import paper_trading_tick_job

    signature = inspect.signature(paper_trading_tick_job)

    for name, parameter in signature.parameters.items():
        assert parameter.kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.VAR_KEYWORD,
        ), f"param {name!r} is not keyword-only"
        assert parameter.default is not inspect.Parameter.empty, (
            f"param {name!r} has no default"
        )
    assert "notifier" in signature.parameters
    assert signature.parameters["notifier"].default is None


def test_paper_trading_tick_job_notify_failure_does_not_propagate(
    patched_scheduler,
    monkeypatch,
):
    module, call_order = patched_scheduler

    def broken_notify(**kwargs):
        raise RuntimeError("simulated notify catastrophe")

    monkeypatch.setattr(module, "notify_paper_tick_events", broken_notify)

    module.paper_trading_tick_job(notifier=CapturingNotifier())

    assert call_order == ["run"]
