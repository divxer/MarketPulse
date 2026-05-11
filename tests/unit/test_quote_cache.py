import time
from datetime import UTC, datetime

from marketpulse.data.quote_cache import QUOTE_CACHE
from marketpulse.data.types import Quote


def _quote(ticker: str, price: float) -> Quote:
    return Quote(
        ticker=ticker, price=price, change_pct=0,
        volume=1, avg_volume_20d=1, fetched_at=datetime.now(UTC),
    )


def test_set_and_get_within_ttl() -> None:
    QUOTE_CACHE.configure(60)
    QUOTE_CACHE.set("AAPL", _quote("AAPL", 200))
    got = QUOTE_CACHE.get("AAPL")
    assert got is not None
    assert got.price == 200


def test_miss_for_unknown_ticker() -> None:
    assert QUOTE_CACHE.get("NEVER_SET") is None


def test_expires_after_ttl() -> None:
    QUOTE_CACHE.configure(60)
    # Set with very short per-entry TTL
    QUOTE_CACHE.set("AAPL", _quote("AAPL", 200), ttl_seconds=0)
    time.sleep(0.01)
    assert QUOTE_CACHE.get("AAPL") is None


def test_clear() -> None:
    QUOTE_CACHE.set("AAPL", _quote("AAPL", 200))
    QUOTE_CACHE.set("NVDA", _quote("NVDA", 300))
    QUOTE_CACHE.clear()
    assert QUOTE_CACHE.get("AAPL") is None
    assert QUOTE_CACHE.get("NVDA") is None
