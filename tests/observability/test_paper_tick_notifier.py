# Layer: stateful
"""6g-T6: notify_paper_tick_events entrypoint integration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'notify.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session


@pytest.fixture
def required_env(monkeypatch):
    from marketpulse.config import get_settings

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@dataclass
class CapturingNotifier:
    sent: list[tuple[str, str, str | None]] = field(default_factory=list)
    return_value: bool = True

    def send(self, title: str, body: str, url: str | None = None) -> bool:
        self.sent.append((title, body, url))
        return self.return_value


class RaisingNotifier:
    def send(self, title: str, body: str, url: str | None = None) -> bool:
        raise RuntimeError("transport down")


def _seed_audit(
    session,
    *,
    event_type,
    timestamp,
    context=None,
    strategy=None,
    order_id=None,
    reason="",
):
    from marketpulse.db.models import PaperAuditEvent

    row = PaperAuditEvent(
        timestamp=timestamp,
        event_type=event_type,
        order_id=order_id,
        strategy=strategy,
        reason=reason,
        context=context or {},
    )
    session.add(row)
    session.flush()
    return row


def _clock(now: datetime):
    from marketpulse.trading.clock import FakeClock

    return FakeClock(now=now)


def _repo(session):
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 22, 18, 0, tzinfo=UTC),
    )
    session.commit()
    return repo


def test_disabled_path_emits_no_notifications(session, required_env, monkeypatch):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "false")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.paper_tick_notifier import (
        NotificationResult,
        notify_paper_tick_events,
    )
    from marketpulse.trading.repository import Repository

    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(
        session,
        event_type="KILL_SWITCH_FLIPPED",
        timestamp=since + timedelta(minutes=10),
        context={"to_state": True, "reason": "drawdown"},
    )
    notifier = CapturingNotifier()

    result = notify_paper_tick_events(
        since=since,
        tick_date=date(2026, 5, 22),
        repository=Repository(session=session),
        notifier=notifier,
        clock=_clock(since + timedelta(minutes=30)),
    )

    assert isinstance(result, NotificationResult)
    assert result.critical_sent == ()
    assert result.summary_sent is False
    assert notifier.sent == []
    assert len(result.failures) == 1
    assert result.failures[0].event_type == "config"
    assert result.failures[0].error == "disabled_by_config"


def test_heartbeat_emits_summary_when_no_audit_rows(
    session,
    required_env,
    monkeypatch,
):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.paper_tick_notifier import notify_paper_tick_events

    repo = _repo(session)
    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    notifier = CapturingNotifier()

    result = notify_paper_tick_events(
        since=since,
        tick_date=date(2026, 5, 22),
        repository=repo,
        notifier=notifier,
        clock=_clock(since + timedelta(minutes=5)),
    )

    assert result.critical_sent == ()
    assert result.summary_sent is True
    assert len(notifier.sent) == 1
    title, _, url = notifier.sent[0]
    assert title.startswith("📊 Paper Tick")
    assert url is None


def test_happy_path_routine_events_summary_only(session, required_env, monkeypatch):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.paper_tick_notifier import notify_paper_tick_events

    repo = _repo(session)
    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(
        session,
        event_type="ORDER_PLACED",
        timestamp=since + timedelta(minutes=5),
        strategy="momentum",
        context={"ticker": "AAPL", "quantity": 10},
    )
    _seed_audit(
        session,
        event_type="ORDER_ENTRY_FILLED",
        timestamp=since + timedelta(minutes=6),
        strategy="momentum",
        context={"ticker": "AAPL", "fill_price": "150.50"},
    )
    _seed_audit(
        session,
        event_type="TICK_COMPLETED",
        timestamp=since + timedelta(minutes=10),
        context={"tick_date": "2026-05-22", "status": "completed"},
    )
    session.commit()
    notifier = CapturingNotifier()

    result = notify_paper_tick_events(
        since=since,
        tick_date=date(2026, 5, 22),
        repository=repo,
        notifier=notifier,
        clock=_clock(since + timedelta(minutes=15)),
    )

    assert result.critical_sent == ()
    assert result.summary_sent is True
    assert len(notifier.sent) == 1
    _, body, _ = notifier.sent[0]
    assert "AAPL × 10 (momentum)" in body
    assert "AAPL @ 150.50" in body
    assert "Status: completed" in body


def test_price_unavailable_attempt_3_critical_plus_summary(
    session,
    required_env,
    monkeypatch,
):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.paper_tick_notifier import notify_paper_tick_events

    repo = _repo(session)
    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=since + timedelta(minutes=3),
        strategy="momentum",
        context={
            "ticker": "AAPL",
            "position_id": 42,
            "attempt_count": 3,
            "horizon_date": "2026-05-22",
            "source": "yfinance",
        },
    )
    _seed_audit(
        session,
        event_type="TICK_COMPLETED",
        timestamp=since + timedelta(minutes=5),
        context={"tick_date": "2026-05-22", "status": "completed"},
    )
    session.commit()
    notifier = CapturingNotifier()

    result = notify_paper_tick_events(
        since=since,
        tick_date=date(2026, 5, 22),
        repository=repo,
        notifier=notifier,
        clock=_clock(since + timedelta(minutes=10)),
    )

    assert [push.event_type for push in result.critical_sent] == [
        "PRICE_UNAVAILABLE"
    ]
    assert result.summary_sent is True
    assert len(notifier.sent) == 2
    assert notifier.sent[0][0] == "⚠️ Position Stuck — AAPL"
    assert notifier.sent[1][0].startswith("📊 Paper Tick")


def test_price_unavailable_attempt_4_suppressed(session, required_env, monkeypatch):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.paper_tick_notifier import notify_paper_tick_events

    repo = _repo(session)
    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=since + timedelta(minutes=3),
        context={"ticker": "AAPL", "position_id": 42, "attempt_count": 4},
    )
    _seed_audit(
        session,
        event_type="TICK_COMPLETED",
        timestamp=since + timedelta(minutes=5),
        context={"tick_date": "2026-05-22", "status": "completed"},
    )
    session.commit()
    notifier = CapturingNotifier()

    result = notify_paper_tick_events(
        since=since,
        tick_date=date(2026, 5, 22),
        repository=repo,
        notifier=notifier,
        clock=_clock(since + timedelta(minutes=10)),
    )

    assert result.critical_sent == ()
    assert result.summary_sent is True
    assert len(notifier.sent) == 1
    assert notifier.sent[0][0].startswith("📊 Paper Tick")


def test_position_closed_with_prior_pu_recovery_critical(
    session,
    required_env,
    monkeypatch,
):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.paper_tick_notifier import notify_paper_tick_events

    repo = _repo(session)
    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=since - timedelta(days=2),
        context={"ticker": "AAPL", "position_id": 42, "attempt_count": 1},
    )
    _seed_audit(
        session,
        event_type="POSITION_CLOSED",
        timestamp=since + timedelta(minutes=3),
        strategy="momentum",
        context={
            "ticker": "AAPL",
            "position_id": 42,
            "exit_price": "152.10",
            "realized_pnl": "21.00",
            "retry_count": 5,
        },
    )
    _seed_audit(
        session,
        event_type="TICK_COMPLETED",
        timestamp=since + timedelta(minutes=5),
        context={"tick_date": "2026-05-22", "status": "completed"},
    )
    session.commit()
    notifier = CapturingNotifier()

    result = notify_paper_tick_events(
        since=since,
        tick_date=date(2026, 5, 22),
        repository=repo,
        notifier=notifier,
        clock=_clock(since + timedelta(minutes=10)),
    )

    assert [push.event_type for push in result.critical_sent] == ["POSITION_CLOSED"]
    assert notifier.sent[0][0] == "✅ Position Recovered — AAPL"


def test_kill_switch_flipped_between_ticks_picked_up(
    session,
    required_env,
    monkeypatch,
):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.paper_tick_notifier import notify_paper_tick_events

    repo = _repo(session)
    prev_tick = datetime(2026, 5, 21, 21, 30, tzinfo=UTC)
    _seed_audit(
        session,
        event_type="TICK_COMPLETED",
        timestamp=prev_tick,
        context={"tick_date": "2026-05-21", "status": "completed"},
    )
    _seed_audit(
        session,
        event_type="KILL_SWITCH_FLIPPED",
        timestamp=datetime(2026, 5, 22, 16, 0, tzinfo=UTC),
        reason="manual",
        context={"to_state": True},
    )
    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(
        session,
        event_type="TICK_COMPLETED",
        timestamp=since + timedelta(minutes=5),
        context={"tick_date": "2026-05-22", "status": "completed"},
    )
    session.commit()
    notifier = CapturingNotifier()

    result = notify_paper_tick_events(
        since=since,
        tick_date=date(2026, 5, 22),
        repository=repo,
        notifier=notifier,
        clock=_clock(since + timedelta(minutes=10)),
    )

    assert "KILL_SWITCH_FLIPPED" in [push.event_type for push in result.critical_sent]
    assert "🛑 Kill Switch FLIPPED" in [title for title, _, _ in notifier.sent]


def test_kill_switch_cycle_skipped_first_skip_emits_then_dedups(
    session,
    required_env,
    monkeypatch,
):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.paper_tick_notifier import notify_paper_tick_events

    repo = _repo(session)
    flip_ts = datetime(2026, 5, 21, 14, 0, tzinfo=UTC)
    _seed_audit(
        session,
        event_type="KILL_SWITCH_FLIPPED",
        timestamp=flip_ts,
        reason="drawdown",
        context={"to_state": True},
    )

    since_first = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(
        session,
        event_type="KILL_SWITCH_CYCLE_SKIPPED",
        timestamp=since_first + timedelta(minutes=2),
        context={
            "tick_date": "2026-05-22",
            "status": "skipped",
            "reason": "kill_switch_active",
        },
    )
    session.commit()

    first_notifier = CapturingNotifier()
    first = notify_paper_tick_events(
        since=since_first,
        tick_date=date(2026, 5, 22),
        repository=repo,
        notifier=first_notifier,
        clock=_clock(since_first + timedelta(minutes=5)),
    )
    assert "KILL_SWITCH_CYCLE_SKIPPED" in [
        push.event_type for push in first.critical_sent
    ]

    since_second = datetime(2026, 5, 23, 21, 0, tzinfo=UTC)
    _seed_audit(
        session,
        event_type="KILL_SWITCH_CYCLE_SKIPPED",
        timestamp=since_second + timedelta(minutes=2),
        context={
            "tick_date": "2026-05-23",
            "status": "skipped",
            "reason": "kill_switch_active",
        },
    )
    session.commit()

    second_notifier = CapturingNotifier()
    second = notify_paper_tick_events(
        since=since_second,
        tick_date=date(2026, 5, 23),
        repository=repo,
        notifier=second_notifier,
        clock=_clock(since_second + timedelta(minutes=5)),
    )
    assert "KILL_SWITCH_CYCLE_SKIPPED" not in [
        push.event_type for push in second.critical_sent
    ]
    assert second.summary_sent is True


def test_engine_invariant_error_admitted_without_tick_date(
    session,
    required_env,
    monkeypatch,
):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.paper_tick_notifier import notify_paper_tick_events

    repo = _repo(session)
    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(
        session,
        event_type="ENGINE_INVARIANT_ERROR",
        timestamp=since + timedelta(minutes=3),
        context={
            "phase": "exit_materialization",
            "order_id": 7,
            "position_id": 12,
            "error": "decimal-mismatch",
            "as_of": "2026-05-22",
        },
    )
    _seed_audit(
        session,
        event_type="TICK_COMPLETED",
        timestamp=since + timedelta(minutes=5),
        context={"tick_date": "2026-05-22", "status": "completed"},
    )
    session.commit()
    notifier = CapturingNotifier()

    result = notify_paper_tick_events(
        since=since,
        tick_date=date(2026, 5, 22),
        repository=repo,
        notifier=notifier,
        clock=_clock(since + timedelta(minutes=10)),
    )

    assert "ENGINE_INVARIANT_ERROR" in [
        push.event_type for push in result.critical_sent
    ]
    assert "🛑 Engine Invariant Error" in [title for title, _, _ in notifier.sent]


def test_notifier_returning_false_is_recorded_and_proceeds(
    session,
    required_env,
    monkeypatch,
):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.paper_tick_notifier import notify_paper_tick_events

    repo = _repo(session)
    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(
        session,
        event_type="KILL_SWITCH_FLIPPED",
        timestamp=since + timedelta(minutes=1),
        reason="drawdown",
        context={"to_state": True},
    )
    _seed_audit(
        session,
        event_type="TICK_COMPLETED",
        timestamp=since + timedelta(minutes=5),
        context={"tick_date": "2026-05-22", "status": "completed"},
    )
    session.commit()
    notifier = CapturingNotifier(return_value=False)

    result = notify_paper_tick_events(
        since=since,
        tick_date=date(2026, 5, 22),
        repository=repo,
        notifier=notifier,
        clock=_clock(since + timedelta(minutes=10)),
    )

    assert result.critical_sent == ()
    assert "send_returned_false" in {failure.error for failure in result.failures}
    assert len(notifier.sent) == 2


def test_notifier_raising_does_not_propagate(session, required_env, monkeypatch):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.paper_tick_notifier import notify_paper_tick_events

    repo = _repo(session)
    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(
        session,
        event_type="TICK_COMPLETED",
        timestamp=since + timedelta(minutes=5),
        context={"tick_date": "2026-05-22", "status": "completed"},
    )
    session.commit()

    result = notify_paper_tick_events(
        since=since,
        tick_date=date(2026, 5, 22),
        repository=repo,
        notifier=RaisingNotifier(),
        clock=_clock(since + timedelta(minutes=10)),
    )

    assert any(failure.error.startswith("send_raised:") for failure in result.failures)


def test_audit_window_boundaries_and_prior_rows(session, required_env, monkeypatch):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.paper_tick_notifier import notify_paper_tick_events

    repo = _repo(session)
    since = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    notify_at = since + timedelta(seconds=10)
    _seed_audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=since - timedelta(microseconds=1),
        context={"position_id": 1, "ticker": "OLD", "attempt_count": 3},
    )
    _seed_audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=since,
        context={"position_id": 2, "ticker": "AAPL", "attempt_count": 3},
    )
    _seed_audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=notify_at,
        context={"position_id": 3, "ticker": "MSFT", "attempt_count": 3},
    )
    session.commit()
    notifier = CapturingNotifier()

    result = notify_paper_tick_events(
        since=since,
        tick_date=date(2026, 5, 22),
        repository=repo,
        notifier=notifier,
        clock=_clock(notify_at),
    )

    titles = [push.title for push in result.critical_sent]
    assert "⚠️ Position Stuck — OLD" not in titles
    assert "⚠️ Position Stuck — AAPL" in titles
    assert "⚠️ Position Stuck — MSFT" in titles
