from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketpulse.db.models import (
    AiAnalysis,
    AppSetting,
    DailyRecap,
    NewsCacheEntry,
    PriceCacheEntry,
    WatchlistItem,
)


def test_create_watchlist_item(db_session: Session) -> None:
    item = WatchlistItem(ticker="AAPL", notes="iphone")
    db_session.add(item)
    db_session.commit()
    assert item.id is not None
    assert item.added_at is not None


def test_daily_recap_unique_date(db_session: Session) -> None:
    today = datetime(2026, 5, 9).date()
    db_session.add(DailyRecap(recap_date=today, generation_status="pending"))
    db_session.commit()
    assert db_session.query(DailyRecap).count() == 1


def test_ai_analysis_with_expiry(db_session: Session) -> None:
    a = AiAnalysis(
        ticker="NVDA",
        model="claude-sonnet-4-6",
        prompt_version="v1",
        input_data_json="{}",
        response_markdown="hello",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db_session.add(a)
    db_session.commit()
    assert a.id is not None


def test_price_cache_composite_pk(db_session: Session) -> None:
    from datetime import date
    db_session.add(
        PriceCacheEntry(
            ticker="AAPL", date=date(2026, 5, 8),
            open=100, high=110, low=99, close=105, volume=1_000_000,
        )
    )
    db_session.commit()
    assert db_session.query(PriceCacheEntry).count() == 1


def test_news_cache_basic(db_session: Session) -> None:
    db_session.add(
        NewsCacheEntry(
            ticker="AAPL",
            headline="x",
            url="https://example.com",
            published_at=datetime.now(UTC),
            source="test",
        )
    )
    db_session.commit()
    assert db_session.query(NewsCacheEntry).count() == 1


def test_app_setting_kv(db_session: Session) -> None:
    db_session.add(AppSetting(key="foo", value="bar"))
    db_session.commit()
    got = db_session.query(AppSetting).filter_by(key="foo").one()
    assert got.value == "bar"


def test_watchlist_ticker_unique(db_session: Session) -> None:
    db_session.add(WatchlistItem(ticker="AAPL"))
    db_session.commit()
    db_session.add(WatchlistItem(ticker="AAPL"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_create_holding(db_session: Session) -> None:
    from marketpulse.db.models import Holding
    h = Holding(ticker="NVDA", quantity=10.5, avg_cost=200.0, notes="core")
    db_session.add(h)
    db_session.commit()
    assert h.id is not None
    assert h.created_at is not None


def test_holding_ticker_unique(db_session: Session) -> None:
    from marketpulse.db.models import Holding
    db_session.add(Holding(ticker="AAPL", quantity=1, avg_cost=180))
    db_session.commit()
    db_session.add(Holding(ticker="AAPL", quantity=2, avg_cost=200))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_daily_recap_unique_date_collision(db_session: Session) -> None:
    today = datetime(2026, 5, 9).date()
    db_session.add(DailyRecap(recap_date=today, generation_status="pending"))
    db_session.commit()
    db_session.add(DailyRecap(recap_date=today, generation_status="pending"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_news_unique_ticker_url(db_session: Session) -> None:
    now = datetime.now(UTC)
    db_session.add(NewsCacheEntry(
        ticker="AAPL", headline="A", url="https://example.com/a",
        published_at=now, source="s",
    ))
    db_session.commit()
    db_session.add(NewsCacheEntry(
        ticker="AAPL", headline="B", url="https://example.com/a",
        published_at=now, source="s",
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_datetime_roundtrip_preserves_utc(db_session: Session) -> None:
    """SQLite needs the TZDateTime decorator to keep tz info on read."""
    now = datetime.now(UTC)
    item = WatchlistItem(ticker="MSFT")
    db_session.add(item)
    db_session.commit()
    db_session.expire_all()
    fetched = db_session.query(WatchlistItem).filter_by(ticker="MSFT").one()
    assert fetched.added_at.tzinfo is not None
    # delta should be tiny — same call site
    assert abs((fetched.added_at - now).total_seconds()) < 5
