"""Trading-day calendar — derived from DB outcomes, not external library."""
from datetime import date


def test_build_calendar_returns_sorted_unique_dates():
    from marketpulse.backtest.trading_calendar import build_calendar
    raw_dates = [
        date(2026, 5, 1),
        date(2026, 5, 5),
        date(2026, 5, 1),   # duplicate
        date(2026, 4, 30),
        date(2026, 5, 3),
    ]
    cal = build_calendar(raw_dates)
    assert cal == [date(2026, 4, 30), date(2026, 5, 1), date(2026, 5, 3), date(2026, 5, 5)]


def test_build_calendar_handles_empty_input():
    from marketpulse.backtest.trading_calendar import build_calendar
    assert build_calendar([]) == []


def test_trading_days_between_inclusive_endpoints():
    from marketpulse.backtest.trading_calendar import build_calendar, trading_days_between
    cal = build_calendar([
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 5, 8),
    ])
    # Between 5/1 and 5/8 inclusive: 5/1, 5/4, 5/5, 5/8 = 4 trading days
    assert trading_days_between(cal, date(2026, 5, 1), date(2026, 5, 8)) == 4


def test_trading_days_between_returns_zero_for_same_day():
    from marketpulse.backtest.trading_calendar import build_calendar, trading_days_between
    cal = build_calendar([date(2026, 5, 1)])
    assert trading_days_between(cal, date(2026, 5, 1), date(2026, 5, 1)) == 1


def test_trading_days_between_excludes_out_of_range():
    from marketpulse.backtest.trading_calendar import build_calendar, trading_days_between
    cal = build_calendar([
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
    ])
    # start before earliest date → only count from earliest
    assert trading_days_between(cal, date(2026, 4, 1), date(2026, 5, 4)) == 2


def test_days_elapsed_fraction_at_entry_is_zero():
    """For MTM linear interp: when current==entry, fraction = 0."""
    from marketpulse.backtest.trading_calendar import build_calendar, elapsed_fraction
    cal = build_calendar([
        date(2026, 5, 1), date(2026, 5, 4),
        date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7),
    ])
    f = elapsed_fraction(cal, entry=date(2026, 5, 1),
                          horizon=date(2026, 5, 7), current=date(2026, 5, 1))
    assert f == 0.0


def test_days_elapsed_fraction_at_horizon_is_one():
    """When current==horizon, fraction = 1."""
    from marketpulse.backtest.trading_calendar import build_calendar, elapsed_fraction
    cal = build_calendar([
        date(2026, 5, 1), date(2026, 5, 4),
        date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7),
    ])
    f = elapsed_fraction(cal, entry=date(2026, 5, 1),
                          horizon=date(2026, 5, 7), current=date(2026, 5, 7))
    assert f == 1.0


def test_days_elapsed_fraction_middle():
    """Halfway through holding period."""
    from marketpulse.backtest.trading_calendar import build_calendar, elapsed_fraction
    cal = build_calendar([
        date(2026, 5, 1), date(2026, 5, 4),
        date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7),
    ])
    # entry=5/1, horizon=5/7, 5 trading days total (5/1, 5/4, 5/5, 5/6, 5/7)
    # current=5/5 → elapsed 3 days / total 5 → fraction = 0.5 (3-1)/(5-1) = 0.5
    import pytest
    f = elapsed_fraction(cal, entry=date(2026, 5, 1),
                          horizon=date(2026, 5, 7), current=date(2026, 5, 5))
    assert f == pytest.approx(0.5, abs=1e-6)
