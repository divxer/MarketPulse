# Layer: invariant
"""6a-1: Clock Protocol + WallClock + FakeClock."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta


def test_wall_clock_now_is_utc_aware():
    from marketpulse.trading.clock import WallClock

    c = WallClock()
    now = c.now()
    assert now.tzinfo is not None
    # WallClock.today() must derive from .now().date(), NOT date.today()
    assert c.today() == now.date()


def test_fake_clock_advance_days():
    from marketpulse.trading.clock import FakeClock

    start = datetime(2026, 5, 21, 17, 30, tzinfo=UTC)
    c = FakeClock(now=start)
    assert c.now() == start
    assert c.today() == date(2026, 5, 21)

    c.advance(days=1)
    assert c.now() == start + timedelta(days=1)
    assert c.today() == date(2026, 5, 22)
