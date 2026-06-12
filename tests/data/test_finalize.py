# Layer: data
"""FinalizeJob — post-close refresh of provisional bars (spec §4)."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from structlog.testing import capture_logs

from marketpulse.data.cache import PriceCache
from marketpulse.data.finalize import finalize_provisional_bars
from marketpulse.data.types import Bar
from marketpulse.db.models import PriceCacheEntry

TODAY = date(2026, 6, 11)  # Thursday, NY business day


def _bar(d: date, close: float = 100.0) -> Bar:
    return Bar(date=d, open=close, high=close, low=close, close=close, volume=1)


def _seed_provisional(db_session, ticker: str, d: date, close: float, monkeypatch):
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(d.year, d.month, d.day, 16, 30, tzinfo=UTC),  # 12:30 ET
    )
    PriceCache(db_session).upsert(ticker, [_bar(d, close)])


class _StubClient:
    """Records calls; returns one settled bar per requested weekday."""

    def __init__(self, fail_tickers: set[str] | None = None) -> None:
        self.calls: list[tuple[str, date, date]] = []
        self.fail_tickers = fail_tickers or set()

    def fetch_history_range(self, ticker: str, *, start: date, end: date) -> list[Bar]:
        self.calls.append((ticker, start, end))
        if ticker in self.fail_tickers:
            raise RuntimeError("boom")
        out, d = [], start
        while d < end:  # end exclusive, matching the real client
            if d.weekday() < 5:
                out.append(_bar(d, close=200.0))
            d += timedelta(days=1)
        return out


def _is_final(db_session, ticker: str, d: date) -> bool:
    return db_session.execute(
        select(PriceCacheEntry.is_final).where(
            PriceCacheEntry.ticker == ticker, PriceCacheEntry.date == d,
        ),
    ).scalar_one()


def test_provisional_rows_flip_final(db_session, monkeypatch):
    _seed_provisional(db_session, "AAPL", TODAY, 291.19, monkeypatch)
    # Two-phase explicit set — NO monkeypatch.undo() (it would revert ALL
    # patches registered so far; re-setattr on the same target is allowed
    # and is the safe idiom). Pin "now" AFTER close so refreshed bars finalize.
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 11, 21, 30, tzinfo=UTC),
    )
    client = _StubClient()
    result = finalize_provisional_bars(db_session, client=client, today_ny=TODAY)
    assert _is_final(db_session, "AAPL", TODAY) is True
    assert result.bars_finalized >= 1
    assert result.failures == 0


def test_spy_always_attempted_even_with_no_provisional_rows(db_session, monkeypatch):
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 11, 21, 30, tzinfo=UTC),
    )
    client = _StubClient()
    finalize_provisional_bars(db_session, client=client, today_ny=TODAY)
    assert any(t == "SPY" for t, _, _ in client.calls)


def test_spy_older_than_window_reached(db_session, monkeypatch):
    # SPY provisional row 10 trading days back — OLDER than the 5-day window.
    old = date(2026, 5, 28)
    _seed_provisional(db_session, "SPY", old, 730.72, monkeypatch)
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 11, 21, 30, tzinfo=UTC),
    )
    client = _StubClient()
    finalize_provisional_bars(db_session, client=client, today_ny=TODAY)
    (ticker, start, end) = next(c for c in client.calls if c[0] == "SPY")
    assert start <= old          # reaches back to the old contamination
    assert end == TODAY + timedelta(days=1)  # end exclusive includes today
    assert _is_final(db_session, "SPY", old) is True


def test_backfill_clamped_to_max_days(db_session, monkeypatch):
    # P1 review guard: an ancient provisional row must NOT trigger a
    # multi-year refetch — start is clamped to today - MAX_BACKFILL_DAYS.
    ancient = date(2019, 1, 2)
    _seed_provisional(db_session, "SPY", ancient, 250.0, monkeypatch)
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 11, 21, 30, tzinfo=UTC),
    )
    client = _StubClient()
    finalize_provisional_bars(db_session, client=client, today_ny=TODAY)
    (_, start, _) = next(c for c in client.calls if c[0] == "SPY")
    assert start >= TODAY - timedelta(days=30)   # clamped, not 2019
    assert _is_final(db_session, "SPY", ancient) is False  # honest: NOT healed


def test_ticker_failure_is_warning_and_isolated(db_session, monkeypatch):
    _seed_provisional(db_session, "AAPL", TODAY, 291.19, monkeypatch)
    _seed_provisional(db_session, "MSFT", TODAY, 389.54, monkeypatch)
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 11, 21, 30, tzinfo=UTC),
    )
    client = _StubClient(fail_tickers={"AAPL"})
    result = finalize_provisional_bars(db_session, client=client, today_ny=TODAY)
    assert result.failures == 1
    assert _is_final(db_session, "AAPL", TODAY) is False   # stays provisional
    assert _is_final(db_session, "MSFT", TODAY) is True    # others unaffected


def test_spy_failure_logs_error(db_session, monkeypatch):
    # Repo pattern: structlog does not propagate to pytest caplog — use
    # structlog.testing.capture_logs (see tests/integration/test_router_telemetry.py).
    _seed_provisional(db_session, "SPY", TODAY, 730.72, monkeypatch)
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 11, 21, 30, tzinfo=UTC),
    )
    client = _StubClient(fail_tickers={"SPY"})
    with capture_logs() as captured:
        result = finalize_provisional_bars(db_session, client=client, today_ny=TODAY)
    assert result.failures == 1
    spy_events = [e for e in captured if e.get("event") == "finalize_spy_failed"]
    assert len(spy_events) == 1
    assert spy_events[0]["log_level"] == "error"
