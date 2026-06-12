# Layer: unit
"""Independent pricing audit core (spec 2026-06-12). Pure — no DB/network."""
from __future__ import annotations

from datetime import date

import pytest

from marketpulse.evaluation.pricing_audit import (
    THRESHOLDS,
    AuditBar,
    FillInput,
    NavDayInput,
    PositionInput,
    run_pricing_audit,
)


def _bars(ticker_days: dict[str, dict[date, tuple[float, float]]]):
    """{ticker: {date: (open, close)}} -> {ticker: [AuditBar...]} sorted by date."""
    out = {}
    for t, days in ticker_days.items():
        out[t] = [
            AuditBar(date=d, open=o, close=c) for d, (o, c) in sorted(days.items())
        ]
    return out


D1, D2, D3 = date(2026, 6, 8), date(2026, 6, 9), date(2026, 6, 10)


def test_fill_bps_exact_and_pass():
    # paper 100.10 vs tencent close 100.00 -> +10 bps exactly.
    fills = [FillInput(fill_id=1, ticker="AAA", side="BUY", quantity=5,
                       price=100.10, trading_date=D1)]
    bars = _bars({"AAA": {D1: (99.0, 100.0), D2: (101.0, 102.0)}})
    r = run_pricing_audit(fills, [], bars)
    f = r.fills
    assert f.n == 1
    assert abs(f.vs_same_day_close.mean_abs_bps - 10.0) < 1e-9
    # next available open AFTER D1 is D2's open 101.0 -> (100.10-101)/101*1e4
    assert abs(f.vs_next_available_open.mean_abs_bps - abs((100.10 - 101.0) / 101.0 * 1e4)) < 1e-6
    assert f.anomalies == ()
    assert r.verdict.fills == "PASS"


def test_fill_anomaly_over_200bps_fails_leg():
    fills = [FillInput(fill_id=1, ticker="AAA", side="BUY", quantity=5,
                       price=103.0, trading_date=D1)]  # +300 bps vs 100.0
    bars = _bars({"AAA": {D1: (99.0, 100.0)}})
    r = run_pricing_audit(fills, [], bars)
    assert len(r.fills.anomalies) == 1
    assert r.verdict.fills == "FAIL"


def test_next_open_never_enters_verdict():
    # same-day-close error tiny; next-open error absurd -> still PASS.
    fills = [FillInput(fill_id=1, ticker="AAA", side="BUY", quantity=5,
                       price=100.001, trading_date=D1)]
    bars = _bars({"AAA": {D1: (99.0, 100.0), D2: (500.0, 500.0)}})
    r = run_pricing_audit(fills, [], bars)
    assert r.fills.vs_next_available_open.mean_abs_bps > 1000
    assert r.verdict.fills == "PASS"


def test_fill_lookback_uses_last_available_close():
    # No bar on D2 (fill date) -> previous close D1 used, mirroring the engine.
    fills = [FillInput(fill_id=1, ticker="AAA", side="BUY", quantity=5,
                       price=100.0, trading_date=D2)]
    bars = _bars({"AAA": {D1: (99.0, 100.0)}})
    r = run_pricing_audit(fills, [], bars)
    assert abs(r.fills.vs_same_day_close.mean_abs_bps - 0.0) < 1e-9


def test_nav_drift_exact_and_gates():
    # cash 1000 + 10 sh AAA @ tencent 101 = 2010 vs recorded 2000 -> +0.5% drift
    nav_days = [NavDayInput(
        trading_date=D1, cash_balance=1000.0, holdings_mtm=1000.0,
        portfolio_nav=2000.0, spy_close=500.0,
        positions=(PositionInput(ticker="AAA", quantity=10),),
    )]
    bars = _bars({"AAA": {D1: (100.0, 101.0)}, "SPY": {D1: (499.0, 500.0)}})
    r = run_pricing_audit([], nav_days, bars)
    n = r.nav
    assert abs(n.per_day[0].drift_pct - 0.5) < 1e-9
    assert abs(n.max_abs_drift_pct - 0.5) < 1e-9
    assert n.max_drift_date == D1
    # 0.5% == max threshold boundary: gate is <=, so PASS at exactly 0.50
    assert r.verdict.nav == ("FAIL" if THRESHOLDS.nav_mean_abs_drift_pct < 0.5 else r.verdict.nav)
    # mean gate: single day mean 0.5 > 0.10 -> nav FAIL
    assert r.verdict.nav == "FAIL"


def test_nav_signed_and_weighted_means():
    # Two days: +0.08% on small holdings day, -0.08% on big holdings day.
    nav_days = [
        NavDayInput(trading_date=D1, cash_balance=900.0, holdings_mtm=100.0,
                    portfolio_nav=1000.0, spy_close=500.0,
                    positions=(PositionInput("AAA", 1),)),
        NavDayInput(trading_date=D2, cash_balance=100.0, holdings_mtm=900.0,
                    portfolio_nav=1000.0, spy_close=500.0,
                    positions=(PositionInput("BBB", 9),)),
    ]
    bars = _bars({
        "AAA": {D1: (0, 100.8)},   # 1 sh: mtm 100.8 vs 100 -> nav 1000.8 -> +0.08%
        "BBB": {D2: (0, 99.911)},  # 9 sh: 899.2 vs 900 -> nav 999.2 -> -0.08%
        "SPY": {D1: (0, 500.0), D2: (0, 500.0)},
    })
    r = run_pricing_audit([], nav_days, bars)
    n = r.nav
    assert n.mean_abs_drift_pct == pytest.approx(0.08, abs=0.005)
    assert abs(n.mean_signed_drift_pct) < 0.01          # signs cancel
    # weighted by holdings_mtm: day2 dominates -> weighted ~0.08 still (both 0.08 abs)
    assert n.weighted_mean_abs_drift_pct == pytest.approx(0.08, abs=0.005)
    assert r.verdict.nav == "PASS"


def test_unpriceable_ticker_visible_not_silent():
    nav_days = [NavDayInput(trading_date=D1, cash_balance=0.0, holdings_mtm=1000.0,
                            portfolio_nav=1000.0, spy_close=500.0,
                            positions=(PositionInput("ZZZ", 10),))]
    bars = _bars({"SPY": {D1: (0, 500.0)}})  # ZZZ missing entirely
    r = run_pricing_audit([], nav_days, bars)
    assert "ZZZ" in r.nav.unpriceable_tickers
    # recorded value kept -> zero drift
    assert r.nav.per_day[0].drift_pct == pytest.approx(0.0)


def test_adjustment_basis_same_sign_ratio():
    # AAA tencent close consistently 1% above recorded-side closes used in fills.
    fills = [FillInput(fill_id=i, ticker="AAA", side="BUY", quantity=1,
                       price=99.0, trading_date=d)
             for i, d in enumerate((D1, D2, D3), start=1)]
    bars = _bars({"AAA": {D1: (0, 100.0), D2: (0, 100.0), D3: (0, 100.0)}})
    r = run_pricing_audit(fills, [], bars)
    row = next(a for a in r.adjustment_basis_analysis if a.ticker == "AAA")
    assert row.same_sign_ratio == 1.0
    assert row.mean_signed_bps == pytest.approx(-100.0, rel=0.01)


def test_one_leg_empty_skipped_and_both_empty_raises():
    bars = _bars({"AAA": {D1: (0, 100.0)}})
    r = run_pricing_audit(
        [FillInput(1, "AAA", "BUY", 1, 100.0, D1)], [], bars,
    )
    assert r.verdict.nav == "SKIPPED"
    assert r.verdict.overall == r.verdict.fills  # gates on remaining leg only
    with pytest.raises(ValueError):
        run_pricing_audit([], [], bars)
