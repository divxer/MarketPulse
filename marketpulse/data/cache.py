from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from marketpulse.data.finality import is_bar_final
from marketpulse.data.types import Bar, NewsItem
from marketpulse.db.models import NewsCacheEntry, PriceCacheEntry


def _now_utc() -> datetime:
    """Module-level for test monkeypatching."""
    return datetime.now(UTC)


class PriceCache:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, ticker: str, bars: list[Bar]) -> None:
        if not bars:
            return
        now = _now_utc()
        rows = []
        for b in bars:
            final = is_bar_final(b.date, now)
            rows.append(
                {
                    "ticker": ticker,
                    "date": b.date,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "fetched_at": now,
                    # Spec §3: finality computed at write time; finalized_at is
                    # this fetch's timestamp ("when obtained"), never job time.
                    "is_final": final,
                    "finalized_at": now if final else None,
                }
            )
        stmt = sqlite_insert(PriceCacheEntry).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "date"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "fetched_at": stmt.excluded.fetched_at,
                "is_final": stmt.excluded.is_final,
                "finalized_at": stmt.excluded.finalized_at,
            },
        )
        self.session.execute(stmt)
        self.session.commit()

    def get_range(self, ticker: str, start: date, end: date) -> list[Bar]:
        stmt = (
            select(PriceCacheEntry)
            .where(PriceCacheEntry.ticker == ticker)
            .where(PriceCacheEntry.date >= start)
            .where(PriceCacheEntry.date <= end)
            .order_by(PriceCacheEntry.date)
        )
        rows = self.session.execute(stmt).scalars().all()
        return [
            Bar(date=r.date, open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume)
            for r in rows
        ]


class NewsCache:
    def __init__(self, session: Session, ttl_days: int = 7) -> None:
        self.session = session
        self.ttl_days = ttl_days

    def upsert(self, items: list[NewsItem]) -> None:
        if not items:
            return
        rows = [
            {
                "ticker": i.ticker,
                "headline": i.headline,
                "url": i.url,
                "published_at": i.published_at,
                "source": i.source,
                "summary": i.summary,
            }
            for i in items
        ]
        stmt = sqlite_insert(NewsCacheEntry).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["ticker", "url"])
        self.session.execute(stmt)
        self.session.commit()

    def recent(self, ticker: str, limit: int) -> list[NewsItem]:
        cutoff = datetime.now(UTC) - timedelta(days=self.ttl_days)
        stmt = (
            select(NewsCacheEntry)
            .where(NewsCacheEntry.ticker == ticker)
            .where(NewsCacheEntry.published_at >= cutoff)
            .order_by(NewsCacheEntry.published_at.desc())
            .limit(limit)
        )
        rows = self.session.execute(stmt).scalars().all()
        return [
            NewsItem(
                ticker=r.ticker,
                headline=r.headline,
                url=r.url,
                published_at=r.published_at,
                source=r.source,
                summary=r.summary,
            )
            for r in rows
        ]

    def purge_expired(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(days=self.ttl_days)
        self.session.query(NewsCacheEntry).filter(NewsCacheEntry.published_at < cutoff).delete()
        self.session.commit()
