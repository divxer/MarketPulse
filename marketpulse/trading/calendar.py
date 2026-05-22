"""Canonical trading-calendar source for Phase 6 (lock xxxii).

ONE library: exchange_calendars. ONE module: this one. Risk gates (6b),
scheduler (6a), market-hours UI (6f) all import from here. No second
source."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

_NY = ZoneInfo("America/New_York")

# Public alias — Phase 6b risk_gates package imports `NY` directly to
# avoid leaking a private symbol across module boundaries.
NY = _NY


class NYTradingCalendar:
    """US equities (XNYS) calendar wrapper.

    Phase 6 default. Lock xxix: timestamps are UTC; market-hours / holiday
    logic is evaluated in the instrument's exchange timezone (Phase 6
    default = America/New_York for US equities).

    Vocabulary discipline (per round-6 review):
        sessions_in_range(a, b)  → trading sessions in [a, b] inclusive
        sessions_after(a, b)     → trading sessions in (a, b] exclusive-start
    Gap detection uses sessions_after — last_processed_tick_date should
    not be re-counted as 'missed'."""

    def __init__(self) -> None:
        self._cal = xcals.get_calendar("XNYS")

    def is_business_day(self, d: date) -> bool:
        return self._cal.is_session(d.isoformat())

    def sessions_in_range(self, a: date, b: date) -> int:
        """Sessions in [a, b] inclusive. If a > b, returns 0."""
        if a > b:
            return 0
        return len(self._cal.sessions_in_range(a.isoformat(), b.isoformat()))

    def sessions_after(self, a: date, b: date) -> int:
        """Sessions in (a, b] — exclusive of start, inclusive of end.
        This is the right primitive for gap detection: if last_tick == today,
        there's nothing 'missed.' If last_tick=Thu and today=Tue across
        Memorial Day, missed = sessions_after(Thu, Tue) - 1 = 1 (Fri 22).
        """
        if a >= b:
            return 0
        # Sessions in (a, b] = sessions in [a+1day, b]
        from datetime import timedelta
        start = a + timedelta(days=1)
        if start > b:
            return 0
        return len(self._cal.sessions_in_range(start.isoformat(), b.isoformat()))

    def next_business_day(self, d: date) -> date:
        """First trading session STRICTLY AFTER d. Works for both
        session and non-session inputs; uses sessions_in_range with a
        forward window rather than next_session (which has documented
        edge-case behavior for non-session inputs in exchange_calendars).
        """
        from datetime import timedelta
        # Look forward up to 10 calendar days — covers any holiday gap.
        end = d + timedelta(days=10)
        sessions = self._cal.sessions_in_range(
            (d + timedelta(days=1)).isoformat(),
            end.isoformat(),
        )
        if len(sessions) == 0:
            raise RuntimeError(
                f"no trading session within 10 days after {d}; calendar gap is unrealistic"
            )
        return sessions[0].date()

    def today_ny_trading_date(self, now_utc: datetime) -> date:
        """Convert a UTC-aware datetime to the NY trading day.

        For a tick fired at 17:30 NY (21:30 UTC) on a Thursday, returns
        that Thursday's date. For non-session NY days (weekend / holiday),
        rolls back to the previous session date — so a tick that
        accidentally fires on Memorial Day produces tick_date == previous
        Friday (avoiding spurious 'today is closed' work).

        Phase 6 default fire time is 17:30 NY (post-close)."""
        if now_utc.tzinfo is None:
            raise ValueError("today_ny_trading_date requires tz-aware datetime")
        ny_now = now_utc.astimezone(_NY)
        ny_date = ny_now.date()
        if self.is_business_day(ny_date):
            return ny_date
        # Roll back to previous session. exchange_calendars'
        # previous_session() requires the input itself to be a session;
        # for non-sessions, use a backward sessions_in_range window.
        from datetime import timedelta
        start = ny_date - timedelta(days=10)
        sessions = self._cal.sessions_in_range(
            start.isoformat(),
            (ny_date - timedelta(days=1)).isoformat(),
        )
        if len(sessions) == 0:
            raise RuntimeError(
                f"no trading session within 10 days before {ny_date}"
            )
        return sessions[-1].date()
