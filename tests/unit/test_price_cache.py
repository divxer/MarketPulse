from datetime import date

from sqlalchemy.orm import Session

from marketpulse.data.cache import PriceCache
from marketpulse.data.types import Bar


def test_upsert_and_read(db_session: Session) -> None:
    cache = PriceCache(db_session)
    bars = [
        Bar(date=date(2026, 5, 6), open=1, high=2, low=0.5, close=1.5, volume=100),
        Bar(date=date(2026, 5, 7), open=1.5, high=2, low=1, close=1.8, volume=120),
    ]
    cache.upsert("AAPL", bars)
    got = cache.get_range("AAPL", date(2026, 5, 6), date(2026, 5, 7))
    assert [b.date for b in got] == [date(2026, 5, 6), date(2026, 5, 7)]
    assert got[0].close == 1.5


def test_upsert_idempotent_same_day(db_session: Session) -> None:
    cache = PriceCache(db_session)
    cache.upsert(
        "AAPL",
        [Bar(date=date(2026, 5, 6), open=1, high=2, low=0.5, close=1.5, volume=100)],
    )
    cache.upsert(
        "AAPL",
        [Bar(date=date(2026, 5, 6), open=1, high=2, low=0.5, close=1.7, volume=200)],
    )
    got = cache.get_range("AAPL", date(2026, 5, 6), date(2026, 5, 6))
    assert len(got) == 1
    assert got[0].close == 1.7
    assert got[0].volume == 200
