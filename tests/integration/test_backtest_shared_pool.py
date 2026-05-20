"""End-to-end orchestrator test — DB seed + shared-pool run."""
from datetime import UTC, date, datetime, timedelta

from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


def _seed(db, *, ticker, strategy, days_ago=10, excess=0.03, horizon=5):
    e = EvaluationEvent(
        event_type="ai_analysis", subtype="bullish", ticker=ticker,
        event_time=datetime.now(UTC) - timedelta(days=days_ago),
        event_price=100.0,
        payload={"source": "stock_analysis", "strategy": strategy,
                 "strategy_version": "v1", "prompt_version": "analysis-v4"},
    )
    db.add(e)
    db.flush()
    db.add(EvaluationOutcome(
        event_id=e.id, horizon_trading_days=horizon,
        event_price=100.0, horizon_price=100.0 * (1 + excess + 0.001),
        horizon_date=date.today() - timedelta(days=max(0, days_ago - horizon)),
        forward_return=excess + 0.001,
        benchmark_ticker="SPY", benchmark_forward_return=0.001,
        excess_return=excess,
    ))


def test_run_shared_pool_returns_triple(db_session):
    """Orchestrator returns {isolated, artifacts, shared} dict."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    assert "isolated" in out
    assert "artifacts" in out
    assert "shared" in out


def test_run_shared_pool_isolated_matches_run_all_backtests(db_session):
    """Phase 4 regression: isolated list shape = same as run_all_backtests."""
    from marketpulse.backtest.simulator import (
        run_all_backtests,
        run_shared_pool_backtest,
    )
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    iso = run_all_backtests(db_session, horizon=5)
    out = run_shared_pool_backtest(db_session, horizon=5)
    assert len(out["isolated"]) == len(iso)
    assert [r.strategy for r in out["isolated"]] == [r.strategy for r in iso]


def test_run_shared_pool_artifacts_parallel_to_isolated_minus_spy(db_session):
    """Artifacts list parallel-indexed to isolated[:-1] (drops SPY)."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    isolated_no_spy = [r for r in out["isolated"] if r.strategy != "__spy_buyhold__"]
    assert len(out["artifacts"]) == len(isolated_no_spy)
    for art, res in zip(out["artifacts"], isolated_no_spy, strict=True):
        assert art.strategy == res.strategy


def test_run_shared_pool_excess_vs_spy_is_pool_cum_minus_spy_cum(db_session):
    """Orchestrator overrides shared.excess_vs_spy with combined - SPY."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    for i in range(3):
        _seed(db_session, ticker=f"T{i}", strategy="momentum_breakout", excess=0.05)
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    spy = next(r for r in out["isolated"] if r.strategy == "__spy_buyhold__")
    expected = out["shared"].cumulative_return - spy.cumulative_return
    assert abs(out["shared"].excess_vs_spy - expected) < 1e-9
