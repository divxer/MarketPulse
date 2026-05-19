"""Backtest DB queries — pull events+outcomes for a strategy/horizon."""
from datetime import UTC, date, datetime, timedelta

from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


def _seed(db, *, ticker="AAPL", subtype="bullish", source="stock_analysis",
          strategy="momentum_breakout", days_ago=10, excess=0.03, horizon=5):
    """Seed one event + matching outcome."""
    e = EvaluationEvent(
        event_type="ai_analysis", subtype=subtype, ticker=ticker,
        event_time=datetime.now(UTC) - timedelta(days=days_ago),
        event_price=100.0,
        payload={"source": source, "strategy": strategy,
                 "strategy_version": "v1", "prompt_version": "analysis-v4"},
    )
    db.add(e)
    db.flush()
    outcome_date = date.today() - timedelta(days=max(0, days_ago - horizon))
    o = EvaluationOutcome(
        event_id=e.id, horizon_trading_days=horizon,
        event_price=100.0, horizon_price=100.0 * (1 + excess + 0.001),
        horizon_date=outcome_date,
        forward_return=excess + 0.001,
        benchmark_ticker="SPY", benchmark_forward_return=0.001,
        excess_return=excess,
    )
    db.add(o)
    db.flush()
    return e, o


def test_get_bullish_events_with_outcomes_filters_by_strategy(db_session):
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    _seed(db_session, ticker="BBB", strategy="fundamental_value")
    db_session.commit()

    rows = get_bullish_events_with_outcomes(
        db_session, strategy="momentum_breakout", horizon=5,
    )
    tickers = [r.ticker for r in rows]
    assert tickers == ["AAA"]


def test_filters_out_neutral_and_bearish(db_session):
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    _seed(db_session, ticker="A1", subtype="bullish")
    _seed(db_session, ticker="A2", subtype="neutral")
    _seed(db_session, ticker="A3", subtype="bearish")
    db_session.commit()

    rows = get_bullish_events_with_outcomes(
        db_session, strategy="momentum_breakout", horizon=5,
    )
    assert len(rows) == 1
    assert rows[0].ticker == "A1"


def test_filters_by_horizon(db_session):
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    e, _ = _seed(db_session, ticker="X", horizon=5)
    o20 = EvaluationOutcome(
        event_id=e.id, horizon_trading_days=20,
        event_price=100.0, horizon_price=110.0,
        horizon_date=date.today(),
        forward_return=0.10, benchmark_ticker="SPY",
        benchmark_forward_return=0.02, excess_return=0.08,
    )
    db_session.add(o20)
    db_session.commit()

    rows_5 = get_bullish_events_with_outcomes(db_session, strategy="momentum_breakout", horizon=5)
    rows_20 = get_bullish_events_with_outcomes(db_session, strategy="momentum_breakout", horizon=20)
    assert len(rows_5) == 1
    assert len(rows_20) == 1
    assert rows_5[0].horizon_price != rows_20[0].horizon_price


def test_filters_by_since(db_session):
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    _seed(db_session, ticker="OLD", days_ago=120)
    _seed(db_session, ticker="NEW", days_ago=10)
    db_session.commit()

    cutoff = date.today() - timedelta(days=90)
    rows = get_bullish_events_with_outcomes(
        db_session, strategy="momentum_breakout", horizon=5, since=cutoff,
    )
    tickers = [r.ticker for r in rows]
    assert tickers == ["NEW"]


def test_filters_out_recap_source(db_session):
    """Spec § Open Decision #14: backtest is stock_analysis only."""
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    _seed(db_session, ticker="STK", source="stock_analysis")
    _seed(db_session, ticker="RCP", source="recap")
    db_session.commit()

    rows = get_bullish_events_with_outcomes(
        db_session, strategy="momentum_breakout", horizon=5,
    )
    tickers = [r.ticker for r in rows]
    assert tickers == ["STK"]


def test_causal_constraint_excludes_future_dated_horizons(db_session):
    """Spec § Open Decision #16: event.event_time.date() < outcome.horizon_date."""
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    e = EvaluationEvent(
        event_type="ai_analysis", subtype="bullish", ticker="BUG",
        event_time=datetime(2026, 5, 10, tzinfo=UTC),
        event_price=100.0,
        payload={"source": "stock_analysis", "strategy": "momentum_breakout",
                 "strategy_version": "v1", "prompt_version": "analysis-v4"},
    )
    db_session.add(e)
    db_session.flush()
    db_session.add(EvaluationOutcome(
        event_id=e.id, horizon_trading_days=5,
        event_price=100.0, horizon_price=105.0,
        horizon_date=date(2026, 5, 5),  # BEFORE event_time
        forward_return=0.05, benchmark_ticker="SPY",
        benchmark_forward_return=0.01, excess_return=0.04,
    ))
    db_session.commit()

    rows = get_bullish_events_with_outcomes(
        db_session, strategy="momentum_breakout", horizon=5,
    )
    assert all(r.ticker != "BUG" for r in rows)


def test_returns_namedtuple_like_with_required_fields(db_session):
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    _seed(db_session, ticker="ZZZ")
    db_session.commit()

    rows = get_bullish_events_with_outcomes(
        db_session, strategy="momentum_breakout", horizon=5,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.ticker == "ZZZ"
    assert r.event_time is not None
    assert r.event_price == 100.0
    assert r.horizon_price > 100.0
    assert r.horizon_date is not None
    assert r.benchmark_forward_return == 0.001


def test_run_all_backtests_returns_7_results(db_session):
    """6 strategies + 1 SPY baseline = 7 results, in strategy display order."""
    from marketpulse.backtest.simulator import run_all_backtests

    strategies_v1 = [
        "fundamental_value", "momentum_breakout", "news_event",
        "sector_rotation", "oversold_reversal", "general",
    ]
    for i, s in enumerate(strategies_v1):
        _seed(db_session, ticker=f"T{i}", strategy=s, days_ago=10, excess=0.03)
    db_session.commit()

    results = run_all_backtests(db_session, horizon=5)
    assert len(results) == 7
    names = [r.strategy for r in results]
    assert "__spy_buyhold__" in names
    for s in strategies_v1:
        assert s in names


def test_run_all_backtests_handles_strategies_with_no_events(db_session):
    from marketpulse.backtest.simulator import run_all_backtests

    _seed(db_session, ticker="ONE", strategy="momentum_breakout")
    db_session.commit()

    results = run_all_backtests(db_session, horizon=5)
    momentum = next(r for r in results if r.strategy == "momentum_breakout")
    assert momentum.n_trades >= 1

    value = next(r for r in results if r.strategy == "fundamental_value")
    assert value.n_trades == 0


def test_run_all_backtests_applies_since_filter(db_session):
    from datetime import timedelta as _td
    from marketpulse.backtest.simulator import run_all_backtests

    _seed(db_session, ticker="OLD", strategy="momentum_breakout", days_ago=120)
    _seed(db_session, ticker="NEW", strategy="momentum_breakout", days_ago=10)
    db_session.commit()

    cutoff = date.today() - _td(days=90)
    results = run_all_backtests(db_session, horizon=5, since=cutoff)
    momentum = next(r for r in results if r.strategy == "momentum_breakout")
    assert momentum.n_trades == 1
