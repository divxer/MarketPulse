# Layer: test
"""PR4 — portfolio_vs_spy_view pure presenter."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from marketpulse.portfolio.north_star import NavSnapshot
from marketpulse.portfolio.portfolio_vs_spy_view import (
    MAX_CHART_POINTS,
    _compute_chart_run,
    _downsample,
    _fmt_excess_label,
    _fmt_index_label,
)


def test_fmt_excess_label_positive():
    assert _fmt_excess_label(Decimal("0.032")) == "+3.2%"


def test_fmt_excess_label_negative():
    assert _fmt_excess_label(Decimal("-0.014")) == "-1.4%"


def test_fmt_excess_label_zero():
    assert _fmt_excess_label(Decimal("0")) == "+0.0%"


def test_fmt_excess_label_none():
    assert _fmt_excess_label(None) == "N/A"


def test_fmt_index_label():
    assert _fmt_index_label(Decimal("1.0413")) == "1.041"


def test_fmt_index_label_none():
    assert _fmt_index_label(None) == "N/A"


def _snap(d, *, port="1.0", spy="1.0", excess="0.0", days=10, sufficient=False):
    """Factory. Pass None (not a string) for port/spy/excess to omit a field."""
    def _dec(x):
        return None if x is None else Decimal(x)
    return NavSnapshot(
        trading_date=d, cash_balance=Decimal("100000"), holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal("100000"), anchor_portfolio_nav=Decimal("100000"),
        portfolio_index=_dec(port), spy_close=Decimal("500"),
        anchor_spy_close=Decimal("500"), spy_index=_dec(spy),
        excess_return=_dec(excess), trading_days_observed=days,
        coverage_ratio=Decimal("0.1"), is_sufficient=sufficient,
        unpriced_positions_count=0, unpriced_tickers=(),
    )


def test_chart_run_all_complete():
    series = [_snap(date(2026, 8, 10 + i)) for i in range(3)]
    run, dropped, excluded = _compute_chart_run(series)
    assert len(run) == 3
    assert dropped == 0
    assert excluded == 0


def test_chart_run_drops_leading_portfolio_only_prefix():
    series = [
        _snap(date(2026, 8, 10), spy=None, excess=None),
        _snap(date(2026, 8, 11), spy=None, excess=None),
        _snap(date(2026, 8, 12)),
        _snap(date(2026, 8, 13)),
    ]
    run, dropped, excluded = _compute_chart_run(series)
    assert [s.trading_date for s in run] == [date(2026, 8, 12), date(2026, 8, 13)]
    assert dropped == 2
    assert excluded == 0


def test_chart_run_truncates_at_midseries_gap():
    series = [
        _snap(date(2026, 8, 12)),
        _snap(date(2026, 8, 13)),
        _snap(date(2026, 8, 14), spy=None, excess=None),
        _snap(date(2026, 8, 15)),
    ]
    run, dropped, excluded = _compute_chart_run(series)
    assert [s.trading_date for s in run] == [date(2026, 8, 12), date(2026, 8, 13)]
    assert dropped == 0
    assert excluded == 2


def test_chart_run_all_incomplete():
    series = [_snap(date(2026, 8, 10 + i), spy=None, excess=None) for i in range(3)]
    run, dropped, excluded = _compute_chart_run(series)
    assert run == []
    assert dropped == 3
    assert excluded == 0


def test_downsample_noop_when_small():
    rows = [_snap(date(2026, 1, 1)) for _ in range(10)]
    out = _downsample(rows)
    assert len(out) == 10
    assert out == rows


def test_downsample_caps_and_preserves_first_last():
    rows = [_snap(date(2026, 1, 1), port=str(1.0 + i / 1000)) for i in range(500)]
    out = _downsample(rows)
    assert len(out) <= MAX_CHART_POINTS
    assert out[0] is rows[0]
    assert out[-1] is rows[-1]


def test_downsample_deterministic():
    rows = [_snap(date(2026, 1, 1), port=str(1.0 + i / 1000)) for i in range(500)]
    assert _downsample(rows) == _downsample(rows)
