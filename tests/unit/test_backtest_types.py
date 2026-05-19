"""StrategyBacktestResult dataclass — frozen, value-equal, Phase 5 hooks."""
from dataclasses import FrozenInstanceError
from datetime import date

import pytest


def _result_kwargs(**overrides):
    """Minimum-viable result kwargs; tests override specific fields."""
    base = {
        "strategy": "momentum_breakout",
        "display_name": "动量突破",
        "horizon": 5,
        "n_trades": 10,
        "n_capacity_skipped": 0,
        "cumulative_return": 0.05,
        "annual_return": 0.12,
        "sharpe": 1.2,
        "sortino": 1.5,
        "max_drawdown": -0.08,
        "calmar": 1.5,
        "win_rate": 0.6,
        "avg_win_pct": 0.03,
        "avg_loss_pct": -0.02,
        "daily_equity_curve": [(date(2026, 4, 1), 10000.0), (date(2026, 5, 1), 10500.0)],
        "excess_vs_spy": 0.02,
    }
    base.update(overrides)
    return base


def test_result_is_frozen():
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs())
    with pytest.raises(FrozenInstanceError):
        r.strategy = "other"


def test_result_required_fields():
    from marketpulse.backtest.types import StrategyBacktestResult
    with pytest.raises(TypeError):
        StrategyBacktestResult()


def test_mtm_model_default_is_linear_interpolation_v0():
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs())
    assert r.mtm_model == "linear_interpolation_v0"


def test_phase5_reserved_fields_default_to_none():
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs())
    assert r.strategy_exposure is None
    assert r.capital_bid_score is None


def test_metric_fields_accept_none():
    """Strategies with n_trades < 5 report None for Sharpe/Sortino/Calmar."""
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs(
        n_trades=2, sharpe=None, sortino=None, calmar=None,
    ))
    assert r.sharpe is None
    assert r.sortino is None
    assert r.calmar is None


def test_excess_vs_spy_can_be_negative():
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs(excess_vs_spy=-0.05))
    assert r.excess_vs_spy == -0.05
