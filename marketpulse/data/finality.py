"""Bar finality — final vs provisional price bars (P2 freshness spec §2).

A bar for D is FINAL iff it was fetched at/after 16:05 America/New_York on D.
Half-days are conservative (provisional until 16:05 despite a 13:00 close) —
never the unsafe direction. Downgrade is impossible: a past date's cutoff is
always before "now".
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
FINAL_CUTOFF_NY = time(16, 5)


def is_bar_final(bar_date: date, fetched_at: datetime) -> bool:
    """True iff `fetched_at` is at/after 16:05 ET on `bar_date`. Naive input is UTC."""
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    # Explicit UTC on BOTH sides — aware-datetime comparison would be correct
    # anyway, but explicit conversion removes any reader doubt and keeps this
    # textually identical to the migration's inlined copy of the rule.
    cutoff_utc = datetime.combine(bar_date, FINAL_CUTOFF_NY, tzinfo=NY).astimezone(UTC)
    return fetched_at.astimezone(UTC) >= cutoff_utc
