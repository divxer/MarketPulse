# Layer: test
"""PR4 — portfolio_vs_spy_view pure presenter."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from marketpulse.portfolio.north_star import NavSnapshot
from marketpulse.portfolio.portfolio_vs_spy_view import (
    MAX_CHART_POINTS,
    VIEWBOX_H,
    VIEWBOX_W,
    _build_chart_data,
    _compute_chart_run,
    _downsample,
    _fmt_excess_label,
    _fmt_index_label,
    build_portfolio_vs_spy_view,
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


def _pts(points_str):
    """Parse 'x,y x,y' -> [(x, y), ...] floats."""
    return [tuple(float(c) for c in pair.split(",")) for pair in points_str.split()]


def test_chart_data_shared_index_scale():
    run = [
        _snap(date(2026, 8, 10), port="1.00", spy="1.00", excess="0.00"),
        _snap(date(2026, 8, 11), port="1.06", spy="1.02", excess="0.04"),
    ]
    cd = _build_chart_data(run)
    assert cd.index_lo == Decimal("1.00")
    assert cd.index_hi == Decimal("1.06")
    port = _pts(cd.portfolio_points)
    spy = _pts(cd.spy_points)
    assert port[1][1] == 0.0          # portfolio 1.06 == hi -> top
    assert port[0][1] == VIEWBOX_H    # portfolio 1.00 == lo -> bottom
    assert 0.0 < spy[1][1] < VIEWBOX_H  # SPY 1.02 between -> proves SHARED scale


def test_chart_data_x_spans_full_width():
    run = [
        _snap(date(2026, 8, 10), port="1.00", spy="1.00", excess="0.00"),
        _snap(date(2026, 8, 11), port="1.01", spy="1.00", excess="0.01"),
        _snap(date(2026, 8, 12), port="1.02", spy="1.00", excess="0.02"),
    ]
    cd = _build_chart_data(run)
    xs = [x for x, _ in _pts(cd.portfolio_points)]
    assert xs[0] == 0.0
    assert xs[-1] == float(VIEWBOX_W)


def test_chart_data_excess_range_contains_zero_positive_only():
    run = [
        _snap(date(2026, 8, 10), port="1.02", spy="1.00", excess="0.02"),
        _snap(date(2026, 8, 11), port="1.05", spy="1.00", excess="0.05"),
    ]
    cd = _build_chart_data(run)
    assert cd.excess_lo == Decimal("0")
    assert cd.excess_hi == Decimal("0.05")
    assert cd.zero_y == VIEWBOX_H   # 0 is the floor -> 0-line at bottom


def test_chart_data_flat_index_guard_no_div_zero():
    run = [
        _snap(date(2026, 8, 10), port="1.00", spy="1.00", excess="0.00"),
        _snap(date(2026, 8, 11), port="1.00", spy="1.00", excess="0.00"),
    ]
    cd = _build_chart_data(run)
    port = _pts(cd.portfolio_points)
    assert all(y == VIEWBOX_H / 2 for _, y in port)


def test_chart_data_all_excess_zero_guard():
    run = [
        _snap(date(2026, 8, 10), port="1.00", spy="1.00", excess="0.00"),
        _snap(date(2026, 8, 11), port="1.02", spy="1.02", excess="0.00"),
    ]
    cd = _build_chart_data(run)
    assert cd.zero_y == VIEWBOX_H / 2


def test_view_e1_empty_series():
    v = build_portfolio_vs_spy_view([])
    assert v.has_data is False
    assert v.chartable is False
    assert v.chart is None
    assert v.show_insufficiency_banner is False
    assert v.hero_excess_return_label == "N/A"
    assert v.coverage_label == "0 / 90"


def test_view_e2_all_spy_none_not_chartable():
    series = [_snap(date(2026, 8, 10 + i), spy=None, excess=None) for i in range(5)]
    v = build_portfolio_vs_spy_view(series)
    assert v.has_data is True
    assert v.chartable is False
    assert v.chart is None
    assert v.chart_start_date is None
    assert v.dropped_prefix_count == 5
    assert v.spy_index_label == "N/A"
    assert v.portfolio_index_label != "N/A"


def test_view_e3_single_chart_point_not_chartable():
    series = [
        _snap(date(2026, 8, 10), spy=None, excess=None),
        _snap(date(2026, 8, 11)),
    ]
    v = build_portfolio_vs_spy_view(series)
    assert v.chartable is False
    assert v.chart is None
    assert v.hero_excess_return is not None


def test_view_e6_latest_missing_spy_na_but_banner_from_sufficiency():
    series = [
        _snap(date(2026, 8, 10), sufficient=True),
        _snap(date(2026, 8, 11), spy=None, excess=None, sufficient=True),
    ]
    v = build_portfolio_vs_spy_view(series)
    assert v.hero_excess_return is None
    assert v.hero_excess_return_label == "N/A"
    assert v.badge == "SUFFICIENT"
    assert v.show_insufficiency_banner is False


def test_view_banner_when_insufficient_but_hero_value_shown():
    series = [
        _snap(date(2026, 8, 10), excess="0.03", sufficient=False),
        _snap(date(2026, 8, 11), excess="0.032", sufficient=False),
    ]
    v = build_portfolio_vs_spy_view(series)
    assert v.show_insufficiency_banner is True
    assert v.badge == "PRELIMINARY"
    assert v.hero_excess_return_label == "+3.2%"


def test_view_e10_chart_present_points_nonempty():
    series = [
        _snap(date(2026, 8, 10), port="1.00", spy="1.00", excess="0.00"),
        _snap(date(2026, 8, 11), port="1.02", spy="1.00", excess="0.02"),
    ]
    v = build_portfolio_vs_spy_view(series)
    assert v.chartable is True
    assert v.chart is not None
    assert v.chart.portfolio_points != ""
    assert v.chart.spy_points != ""
    assert v.chart.excess_points != ""
