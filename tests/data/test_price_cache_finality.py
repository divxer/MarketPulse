# Layer: data
"""Write-time finality in PriceCache.upsert (spec §3)."""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select

from marketpulse.data.cache import PriceCache
from marketpulse.data.types import Bar
from marketpulse.db.models import PriceCacheEntry


def _bar(d: date, close: float = 100.0) -> Bar:
    return Bar(date=d, open=close, high=close, low=close, close=close, volume=1)


def _row(db_session, ticker: str, d: date) -> PriceCacheEntry:
    return db_session.execute(
        select(PriceCacheEntry).where(
            PriceCacheEntry.ticker == ticker, PriceCacheEntry.date == d,
        ),
    ).scalar_one()


def test_intraday_bar_written_provisional(db_session, monkeypatch):
    cache = PriceCache(db_session)
    intraday = datetime(2026, 6, 10, 16, 30, tzinfo=UTC)  # 12:30 ET
    monkeypatch.setattr("marketpulse.data.cache._now_utc", lambda: intraday)
    cache.upsert("SPY", [_bar(date(2026, 6, 10))])
    row = _row(db_session, "SPY", date(2026, 6, 10))
    assert row.is_final is False
    assert row.finalized_at is None


def test_after_close_bar_written_final_with_finalized_at_eq_fetched_at(db_session, monkeypatch):
    cache = PriceCache(db_session)
    evening = datetime(2026, 6, 10, 21, 30, tzinfo=UTC)  # 17:30 ET
    monkeypatch.setattr("marketpulse.data.cache._now_utc", lambda: evening)
    cache.upsert("SPY", [_bar(date(2026, 6, 10))])
    row = _row(db_session, "SPY", date(2026, 6, 10))
    assert row.is_final is True
    # finalized_at == fetched_at: "when obtained from source", never job time.
    assert row.finalized_at == row.fetched_at


def test_provisional_row_flips_final_on_post_close_refetch(db_session, monkeypatch):
    cache = PriceCache(db_session)
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 10, 16, 30, tzinfo=UTC),
    )
    cache.upsert("SPY", [_bar(date(2026, 6, 10), close=730.72)])   # midday
    # Two-phase explicit set — NO monkeypatch.undo() (it would revert ALL
    # patches; re-setattr on the same target is the safe idiom).
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 10, 21, 30, tzinfo=UTC),
    )
    cache.upsert("SPY", [_bar(date(2026, 6, 10), close=725.10)])   # true close
    row = _row(db_session, "SPY", date(2026, 6, 10))
    assert row.is_final is True
    assert row.close == 725.10  # OHLCV overwritten atomically with the flip


def test_past_date_bar_always_final(db_session, monkeypatch):
    cache = PriceCache(db_session)
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 11, 14, 0, tzinfo=UTC),  # intraday TODAY
    )
    cache.upsert("AAPL", [_bar(date(2026, 6, 10))])        # YESTERDAY's bar
    assert _row(db_session, "AAPL", date(2026, 6, 10)).is_final is True
