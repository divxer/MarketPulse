"""Clock dependency for the trading layer (lock xxiii).

Production uses WallClock; tests use FakeClock. No production code in
marketpulse.trading.* or marketpulse.scheduler.paper_trading_tick may
call date.today() or datetime.now() directly — they MUST go through an
injected Clock (lock xxiii)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def today(self) -> date: ...


class WallClock:
    """Production clock. Always returns UTC-aware datetimes."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        # Derive from .now() — preserves the tz invariant.
        return self.now().date()


class FakeClock:
    """Test clock. Caller controls time."""

    def __init__(self, *, now: datetime) -> None:
        assert now.tzinfo is not None, "FakeClock requires tz-aware datetime"
        self._now = now

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    def advance(self, *, days: int = 0, seconds: int = 0) -> None:
        self._now = self._now + timedelta(days=days, seconds=seconds)

    def set(self, *, now: datetime) -> None:
        assert now.tzinfo is not None
        self._now = now
