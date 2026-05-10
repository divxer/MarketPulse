from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from marketpulse.data.cache import NewsCache
from marketpulse.data.types import NewsItem


def test_upsert_dedup_and_recent(db_session: Session) -> None:
    cache = NewsCache(db_session, ttl_days=7)
    now = datetime.now(UTC)
    items = [
        NewsItem(ticker="AAPL", headline="A", url="https://a.com", published_at=now, source="x"),
        NewsItem(ticker="AAPL", headline="B", url="https://b.com", published_at=now, source="x"),
    ]
    cache.upsert(items)
    cache.upsert(items)  # same urls -> no duplicates
    recent = cache.recent("AAPL", limit=10)
    assert len(recent) == 2
    assert {n.url for n in recent} == {"https://a.com", "https://b.com"}


def test_purge_expired(db_session: Session) -> None:
    cache = NewsCache(db_session, ttl_days=7)
    old = datetime.now(UTC) - timedelta(days=10)
    cache.upsert([NewsItem(ticker="A", headline="x", url="u", published_at=old, source="s")])
    cache.purge_expired()
    assert cache.recent("A", limit=10) == []
