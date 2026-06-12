# Layer: unit
"""Finality rule — spec §2: final iff fetched >= 16:05 America/New_York on bar date."""
from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from marketpulse.data.finality import FINAL_CUTOFF_NY, is_bar_final

NY = ZoneInfo("America/New_York")


def test_intraday_fetch_is_provisional_edt():
    # 2026-06-10 is EDT (UTC-4): 12:30 ET == 16:30 UTC — before 16:05 ET.
    assert is_bar_final(date(2026, 6, 10), datetime(2026, 6, 10, 16, 30, tzinfo=UTC)) is False


def test_after_close_fetch_is_final_edt():
    # 17:30 ET == 21:30 UTC on an EDT date.
    assert is_bar_final(date(2026, 6, 10), datetime(2026, 6, 10, 21, 30, tzinfo=UTC)) is True


def test_exactly_at_cutoff_is_final():
    cutoff_utc = datetime(2026, 6, 10, 16, 5, tzinfo=NY).astimezone(UTC)
    assert is_bar_final(date(2026, 6, 10), cutoff_utc) is True


def test_est_winter_date_cutoff():
    # 2026-01-15 is EST (UTC-5): 16:05 ET == 21:05 UTC.
    assert is_bar_final(date(2026, 1, 15), datetime(2026, 1, 15, 21, 4, tzinfo=UTC)) is False
    assert is_bar_final(date(2026, 1, 15), datetime(2026, 1, 15, 21, 6, tzinfo=UTC)) is True


def test_naive_datetime_treated_as_utc():
    # SQLite round-trips naive timestamps; rule must treat them as UTC.
    assert is_bar_final(date(2026, 6, 10), datetime(2026, 6, 10, 16, 30)) is False
    assert is_bar_final(date(2026, 6, 10), datetime(2026, 6, 10, 21, 30)) is True


def test_past_date_always_final():
    # Any fetch NOW of yesterday's bar is final — downgrade is impossible.
    assert is_bar_final(date(2026, 6, 9), datetime(2026, 6, 10, 14, 0, tzinfo=UTC)) is True


def test_cutoff_constant():
    assert FINAL_CUTOFF_NY.hour == 16 and FINAL_CUTOFF_NY.minute == 5
