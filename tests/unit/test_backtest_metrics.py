"""Metrics module — empyrical-reloaded wrappers on daily return series."""
from datetime import date, timedelta

import pytest


def _equity_curve(start_value=10_000, daily_returns=None):
    """Build an equity_curve list[(date, float)] from daily returns."""
    daily_returns = daily_returns or []
    start = date(2026, 4, 1)
    curve = [(start, float(start_value))]
    v = start_value
    for i, r in enumerate(daily_returns, start=1):
        v *= (1 + r)
        curve.append((start + timedelta(days=i), v))
    return curve


def test_compute_returns_none_metrics_when_n_trades_below_threshold():
    """Spec § Metrics: metrics are None when n_trades < 5."""
    from marketpulse.backtest.metrics import compute_metrics
    curve = _equity_curve(daily_returns=[0.01, 0.02])
    m = compute_metrics(equity_curve=curve, n_trades=2, trade_returns=[0.05, 0.03])
    assert m.sharpe is None
    assert m.sortino is None
    assert m.calmar is None


def test_compute_returns_real_metrics_when_n_trades_above_threshold():
    from marketpulse.backtest.metrics import compute_metrics
    curve = _equity_curve(daily_returns=[0.005] * 30)
    m = compute_metrics(equity_curve=curve, n_trades=10,
                       trade_returns=[0.005] * 10)
    assert m.sharpe is not None
    assert m.sharpe > 0
    assert m.cumulative_return > 0


def test_max_drawdown_is_negative_on_drawdown_path():
    from marketpulse.backtest.metrics import compute_metrics
    curve = _equity_curve(daily_returns=[0.05] + [-0.02] * 10)
    m = compute_metrics(equity_curve=curve, n_trades=10,
                       trade_returns=[0.05] * 5 + [-0.02] * 5)
    assert m.max_drawdown < 0


def test_win_rate_computed_from_trade_returns():
    from marketpulse.backtest.metrics import compute_metrics
    curve = _equity_curve(daily_returns=[0.01] * 10)
    returns = [0.05, 0.03, -0.02, 0.01, -0.04, 0.02, 0.01, -0.01, 0.03, 0.05]
    m = compute_metrics(equity_curve=curve, n_trades=10, trade_returns=returns)
    assert m.win_rate == pytest.approx(0.7, abs=1e-6)


def test_avg_win_pct_is_mean_of_positive_trades():
    from marketpulse.backtest.metrics import compute_metrics
    curve = _equity_curve(daily_returns=[0.0] * 5)
    m = compute_metrics(equity_curve=curve, n_trades=5,
                       trade_returns=[0.05, 0.10, -0.02, 0.06, -0.04])
    assert m.avg_win_pct == pytest.approx(0.07, abs=1e-6)


def test_avg_loss_pct_is_mean_of_negative_trades_negative_sign():
    from marketpulse.backtest.metrics import compute_metrics
    curve = _equity_curve(daily_returns=[0.0] * 5)
    m = compute_metrics(equity_curve=curve, n_trades=5,
                       trade_returns=[0.05, -0.02, 0.06, -0.04, -0.06])
    assert m.avg_loss_pct == pytest.approx(-0.04, abs=1e-6)


def test_zero_trades_returns_zeroed_metrics():
    """No bullish events at all — empty equity curve."""
    from marketpulse.backtest.metrics import compute_metrics
    m = compute_metrics(equity_curve=[(date(2026, 5, 1), 10_000.0)],
                       n_trades=0, trade_returns=[])
    assert m.cumulative_return == 0.0
    assert m.win_rate == 0.0
    assert m.sharpe is None
