"""Module-level in-memory cache for live quotes.

Why module-level (not on DataService): DataService is instantiated per
request via FastAPI's Depends, so an instance-level cache would be useless.
A module-level dict shared across requests + the scheduler thread is what
we want. Thread-safe via a single lock.

TTL defaults to 5 minutes — long enough to cut yfinance request volume by
~10x for a typical page load, short enough that 15-min-delayed quote data
is still "fresh" by the time the cache expires.
"""

import threading
import time
from dataclasses import dataclass

from marketpulse.data.types import Quote


@dataclass
class _Entry:
    quote: Quote
    expires_at: float


class _Cache:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl = ttl_seconds
        self._store: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def get(self, ticker: str) -> Quote | None:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(ticker)
            if entry is None or entry.expires_at <= now:
                return None
            return entry.quote

    def set(self, ticker: str, quote: Quote, ttl_seconds: int | None = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl
        with self._lock:
            self._store[ticker] = _Entry(
                quote=quote, expires_at=time.monotonic() + ttl,
            )

    def clear(self) -> None:
        """Reset cache (mainly for tests)."""
        with self._lock:
            self._store.clear()

    def configure(self, ttl_seconds: int) -> None:
        """Allow startup-time TTL override from settings."""
        self.ttl = ttl_seconds


# Single module-level instance shared by all DataService consumers.
QUOTE_CACHE = _Cache()
