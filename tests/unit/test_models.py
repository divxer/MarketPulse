from datetime import UTC, datetime, timedelta

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
