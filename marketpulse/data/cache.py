from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from marketpulse.data.types import Bar, NewsItem
from marketpulse.db.models import NewsCacheEntry, PriceCacheEntry


class PriceCache:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, ticker: str, bars: list[Bar]) -> None:
        if not bars:
            return
        rows = [
            {
                "ticker": ticker,
                "date": b.date,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "fetched_at": datetime.now(UTC),
            }
            for b in bars
        ]
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
