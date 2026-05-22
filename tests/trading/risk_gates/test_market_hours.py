# Layer: pure
"""6b-T11: MarketHoursGate tests (file created in T3 for the NY import)."""

from __future__ import annotations


def test_calendar_module_exports_ny_zoneinfo():
    """T3: NY tz alias must be publicly importable for risk_gates package."""
    from zoneinfo import ZoneInfo

    from marketpulse.trading.calendar import NY
    assert NY == ZoneInfo("America/New_York")  # noqa: SIM300
