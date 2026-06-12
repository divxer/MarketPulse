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


# --- Task 2: DB loaders ---


def _seed_position_chain(
    session,
    *,
    ticker,
    qty,
    opened,
    price="100",
    closed=None,
):
    """PaperOrder + PaperPosition + PaperFill chain (test_query_models shape)."""
    from datetime import timedelta
    from decimal import Decimal

    from marketpulse.db.models import PaperFill, PaperOrder, PaperPosition

    order = PaperOrder(
        idempotency_key=f"{ticker}-{opened.isoformat()}",
        allocation_run_id=f"test-run-{opened.date().isoformat()}",
        strategy="general",
        ticker=ticker,
        quantity=qty,
        event_time=opened,
        allocation_date=opened.date(),
        horizon_date=opened.date() + timedelta(days=7),
        placed_at=opened,
        filled_at=opened,
        cancelled_at=None,
        cancel_reason=None,
        event_price=Decimal(price),
        horizon_price=None,
        status="ENTRY_FILLED",
        strategy_version="v1",
        allocator_version="v1",
        execution_engine_version="v1",
        weight=Decimal("1"),
    )
    session.add(order)
    session.flush()
    pos = PaperPosition(
        order_id=order.id,
        entry_fill_id=None, exit_fill_id=None,
        strategy="general", ticker=ticker, quantity=qty,
        entry_price=Decimal(price), entry_date=opened.date(),
        horizon_date=opened.date() + timedelta(days=7),
        status="OPEN",
        opened_at=opened, closed_at=None,
        exit_price=None,
        realized_pnl=None,
    )
    session.add(pos)
    session.flush()
    entry_fill = PaperFill(
        order_id=order.id, position_id=pos.id, side="ENTRY",
        price=Decimal(price), quantity=qty, filled_at=opened,
        cash_delta=-Decimal(price) * qty, realized_pnl=None,
    )
    session.add(entry_fill)
    session.flush()
    pos.entry_fill_id = entry_fill.id
    if closed is not None:
        exit_fill = PaperFill(
            order_id=order.id, position_id=pos.id, side="EXIT",
            price=Decimal("105"), quantity=qty, filled_at=closed,
            cash_delta=Decimal("105") * qty,
            realized_pnl=Decimal("5") * qty,
        )
        session.add(exit_fill)
        session.flush()
        pos.exit_fill_id = exit_fill.id
        pos.status = "CLOSED"
        pos.closed_at = closed
        pos.exit_price = Decimal("105")
        pos.realized_pnl = Decimal("5") * qty
    session.flush()
    return pos


def _seed_nav_snapshot(session, *, trading_date, cash, mtm, nav, spy_close):
    from datetime import UTC, datetime
    from decimal import Decimal

    from marketpulse.db.models import PaperNavSnapshot

    now = datetime.now(UTC)
    session.add(PaperNavSnapshot(
        trading_date=trading_date,
        cash_balance=Decimal(cash),
        holdings_mtm=Decimal(mtm),
        portfolio_nav=Decimal(nav),
        anchor_portfolio_nav=Decimal(nav),
        portfolio_index=Decimal("1"),
        spy_close=Decimal(spy_close) if spy_close is not None else None,
        anchor_spy_close=None,
        spy_index=None,
        excess_return=None,
        trading_days_observed=1,
        coverage_ratio=Decimal("1"),
        is_sufficient=True,
        created_at=now,
        updated_at=now,
    ))
    session.flush()


def test_load_fills_joins_ticker_and_ny_trading_date(db_session):
    from datetime import UTC, datetime

    from marketpulse.evaluation.pricing_audit import load_fills

    # 2026-06-09 01:00 UTC == 2026-06-08 21:00 New York -> NY date 06-08.
    opened = datetime(2026, 6, 9, 1, 0, tzinfo=UTC)
    _seed_position_chain(
        db_session, ticker="AAPL", qty=5, opened=opened, price="100.10",
    )
    db_session.commit()

    fills = load_fills(db_session)

    assert len(fills) == 1
    f = fills[0]
    assert f.ticker == "AAPL"          # joined from PaperPosition
    assert f.side == "ENTRY"
    assert f.quantity == 5
    assert isinstance(f.price, float)
    assert f.price == pytest.approx(100.10)
    assert f.trading_date == date(2026, 6, 8)  # UTC -> NY conversion


def test_load_fills_includes_exit_fills(db_session):
    from datetime import UTC, datetime

    from marketpulse.evaluation.pricing_audit import load_fills

    opened = datetime(2026, 6, 8, 20, 0, tzinfo=UTC)   # NY 16:00 06-08
    closed = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)  # NY 16:00 06-10
    _seed_position_chain(
        db_session, ticker="MSFT", qty=3, opened=opened, closed=closed,
    )
    db_session.commit()

    fills = load_fills(db_session)

    assert [f.side for f in fills] == ["ENTRY", "EXIT"]
    assert {f.ticker for f in fills} == {"MSFT"}
    assert fills[1].trading_date == date(2026, 6, 10)
    assert fills[1].price == pytest.approx(105.0)


def test_load_nav_days_positions_as_of_and_floats(db_session):
    from datetime import UTC, datetime

    from marketpulse.evaluation.pricing_audit import load_nav_days

    # AAPL opened 06-08; MSFT opened 06-10 -> not in 06-09 as-of set.
    _seed_position_chain(
        db_session, ticker="AAPL", qty=5,
        opened=datetime(2026, 6, 8, 14, 0, tzinfo=UTC),
    )
    _seed_position_chain(
        db_session, ticker="MSFT", qty=3,
        opened=datetime(2026, 6, 10, 14, 0, tzinfo=UTC),
    )
    _seed_nav_snapshot(
        db_session, trading_date=date(2026, 6, 9),
        cash="500", mtm="500.5", nav="1000.5", spy_close="600.25",
    )
    _seed_nav_snapshot(
        db_session, trading_date=date(2026, 6, 10),
        cash="200", mtm="800", nav="1000", spy_close=None,
    )
    db_session.commit()

    nav_days = load_nav_days(db_session)

    assert [nd.trading_date for nd in nav_days] == [
        date(2026, 6, 9), date(2026, 6, 10),
    ]
    d1, d2 = nav_days
    assert isinstance(d1.cash_balance, float)
    assert d1.cash_balance == pytest.approx(500.0)
    assert d1.holdings_mtm == pytest.approx(500.5)
    assert d1.portfolio_nav == pytest.approx(1000.5)
    assert d1.spy_close == pytest.approx(600.25)
    assert {p.ticker for p in d1.positions} == {"AAPL"}  # MSFT not yet open
    assert all(isinstance(p.quantity, float) for p in d1.positions)
    assert d2.spy_close is None
    assert {p.ticker for p in d2.positions} == {"AAPL", "MSFT"}
    assert dict((p.ticker, p.quantity) for p in d2.positions)["MSFT"] == pytest.approx(3.0)


def test_loaders_empty_db(db_session):
    from marketpulse.evaluation.pricing_audit import load_fills, load_nav_days

    assert load_fills(db_session) == []
    assert load_nav_days(db_session) == []
