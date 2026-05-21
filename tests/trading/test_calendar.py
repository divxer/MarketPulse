# Layer: invariant
"""6a-1: NYTradingCalendar invariants and smoke."""
from __future__ import annotations

from datetime import date

import pytest


def test_exchange_calendars_importable():
    """exchange_calendars is the locked dependency for lock xxxii."""
    import exchange_calendars
    assert exchange_calendars is not None
