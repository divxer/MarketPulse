"""Metrics on daily return series, computed via empyrical-reloaded.

Spec § Open Decision #8: Sharpe / Sortino / Calmar are computed on
DAILY return series (NOT on irregular-spacing trade returns) to
avoid the per-trade-spacing Sharpe inflation bug.

Sample threshold: n_trades < 5 returns None for risk-adjusted
metrics (Sharpe / Sortino / Calmar). Cumulative_return / annual_return
/ max_drawdown / win_rate are always computed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from empyrical import (
    annual_return,
    calmar_ratio,
    cum_returns_final,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
)

# Spec § Metrics: floor for risk-adjusted ratios.
MIN_TRADES_FOR_RISK_METRICS = 5


@dataclass(frozen=True)
class BacktestMetrics:
    """Computed metrics block — fed into StrategyBacktestResult."""

    cumulative_return: float
    annual_return: float
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    calmar: float | None
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float


def compute_metrics(
    *,
    equity_curve: list[tuple[date, float]],
    n_trades: int,
    trade_returns: list[float],
) -> BacktestMetrics:
    """Compute all metrics from a daily equity curve + trade list.

    Args:
        equity_curve: list of (date, portfolio_value) sorted ASC.
        n_trades: number of trades executed (used for sample-size floor).
        trade_returns: per-trade realized returns (used for win_rate /
            avg_win_pct / avg_loss_pct). Length = n_trades.

    Returns:
        BacktestMetrics with all 9 fields populated.
    """
    if len(equity_curve) < 2:
        return BacktestMetrics(
            cumulative_return=0.0,
            annual_return=0.0,
            sharpe=None,
            sortino=None,
            max_drawdown=0.0,
            calmar=None,
            win_rate=_win_rate(trade_returns) if trade_returns else 0.0,
            avg_win_pct=_avg_win(trade_returns) if trade_returns else 0.0,
            avg_loss_pct=_avg_loss(trade_returns) if trade_returns else 0.0,
        )

    values = np.array([v for _, v in equity_curve], dtype=float)
    daily_returns = np.diff(values) / values[:-1]

    cum_ret = float(cum_returns_final(daily_returns))
    annual = float(annual_return(daily_returns))
    mdd = float(max_drawdown(daily_returns))

    if n_trades >= MIN_TRADES_FOR_RISK_METRICS:
        s = float(sharpe_ratio(daily_returns))
        so = float(sortino_ratio(daily_returns))
        c = float(calmar_ratio(daily_returns))
        s = None if not np.isfinite(s) else s
        so = None if not np.isfinite(so) else so
        c = None if not np.isfinite(c) else c
    else:
        s = so = c = None

    return BacktestMetrics(
        cumulative_return=cum_ret,
        annual_return=annual,
        sharpe=s,
        sortino=so,
        max_drawdown=mdd,
        calmar=c,
        win_rate=_win_rate(trade_returns),
        avg_win_pct=_avg_win(trade_returns),
        avg_loss_pct=_avg_loss(trade_returns),
    )


def _win_rate(trade_returns: list[float]) -> float:
    if not trade_returns:
        return 0.0
    wins = sum(1 for r in trade_returns if r > 0)
    return wins / len(trade_returns)


def _avg_win(trade_returns: list[float]) -> float:
    wins = [r for r in trade_returns if r > 0]
    return sum(wins) / len(wins) if wins else 0.0


def _avg_loss(trade_returns: list[float]) -> float:
    losses = [r for r in trade_returns if r < 0]
    return sum(losses) / len(losses) if losses else 0.0
