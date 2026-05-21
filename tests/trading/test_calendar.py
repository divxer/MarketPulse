# Layer: invariant
"""6a-1: NYTradingCalendar invariants and smoke."""
from __future__ import annotations

from datetime import UTC, date, datetime


def test_exchange_calendars_importable():
    """exchange_calendars is the locked dependency for lock xxxii."""
    import exchange_calendars
    assert exchange_calendars is not None


def test_sessions_in_range_inclusive_count():
    """sessions_in_range(a, b) returns trading sessions in [a, b] inclusive."""
    from marketpulse.trading.calendar import NYTradingCalendar

    cal = NYTradingCalendar()
    # 2026-05-21 (Thu) → 2026-05-26 (Tue). Memorial Day 2026 is Mon 25.
    # Sessions: Thu 21, Fri 22, (Mon 25 closed), Tue 26 — 3 sessions inclusive.
    assert cal.sessions_in_range(date(2026, 5, 21), date(2026, 5, 26)) == 3
    # Single-day range covering a session.
    assert cal.sessions_in_range(date(2026, 5, 21), date(2026, 5, 21)) == 1
    # a > b → 0.
    assert cal.sessions_in_range(date(2026, 5, 26), date(2026, 5, 21)) == 0


def test_sessions_after_excludes_start_includes_end():
    """sessions_after(a, b) = sessions in (a, b] — what gap detection
    needs. Returns 0 when last_tick == today (no missed days)."""
    from marketpulse.trading.calendar import NYTradingCalendar

    cal = NYTradingCalendar()
    # Thu 21 → Fri 22 → 1 session after Thu (Fri itself).
    assert cal.sessions_after(date(2026, 5, 21), date(2026, 5, 22)) == 1
    # Same day → 0 (no gap).
    assert cal.sessions_after(date(2026, 5, 21), date(2026, 5, 21)) == 0
    # Thu 21 → Tue 26 across Memorial Day → Fri 22 + Tue 26 = 2 sessions.
    assert cal.sessions_after(date(2026, 5, 21), date(2026, 5, 26)) == 2


def test_next_business_day_semantics():
    """next_business_day(d) = the first trading session STRICTLY AFTER d.

    Test cases:
    - Thu (session) → Fri (next session)
    - Fri before Memorial-Day-Monday → Tue (skip closed Mon)
    - Sat (non-session) → following Mon if open, else next session
    """
    from marketpulse.trading.calendar import NYTradingCalendar

    cal = NYTradingCalendar()

    # Thu 2026-05-21 → Fri 2026-05-22 (both sessions)
    assert cal.next_business_day(date(2026, 5, 21)) == date(2026, 5, 22)

    # Fri before Memorial Day 2026 (Mon 25 closed): Fri 22 → Tue 26
    assert cal.next_business_day(date(2026, 5, 22)) == date(2026, 5, 26)

    # Non-session input — Sat 2026-05-23: → next session (Tue 26 since Mon closed)
    assert cal.next_business_day(date(2026, 5, 23)) == date(2026, 5, 26)


def test_ny_calendar_today_ny_trading_date_for_post_close_utc():
    from marketpulse.trading.calendar import NYTradingCalendar

    cal = NYTradingCalendar()
    # 21:30 UTC on 2026-05-21 is 17:30 NY (post-close on the same NY day).
    utc_post_close = datetime(2026, 5, 21, 21, 30, tzinfo=UTC)
    assert cal.today_ny_trading_date(utc_post_close) == date(2026, 5, 21)


def test_today_ny_trading_date_on_non_session_rolls_back():
    """If clock fires on a non-session NY day (e.g., a holiday), the
    tick_date rolls back to the previous session — preventing accidental
    work on closed days."""
    from marketpulse.trading.calendar import NYTradingCalendar

    cal = NYTradingCalendar()
    # 2026-05-25 (Mon) is Memorial Day, closed. 22:00 UTC = 18:00 NY.
    holiday_utc = datetime(2026, 5, 25, 22, 0, tzinfo=UTC)
    # Previous session = Fri 2026-05-22.
    assert cal.today_ny_trading_date(holiday_utc) == date(2026, 5, 22)
