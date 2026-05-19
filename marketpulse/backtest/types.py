"""StrategyBacktestResult — frozen dataclass returned by the simulator."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class StrategyBacktestResult:
    """One per-strategy (or SPY) backtest run result.

    All Sharpe/Sortino/Calmar fields are None when n_trades < 5
    (insufficient sample). daily_equity_curve is downsampled to ~120
    points before being returned (see simulator.downsample_equity_curve()).

    Fields are 3-layered: identity, performance, trade-level. Phase 5
    reserved hooks at bottom (always None in v0).
    """

    # Identity
    strategy: str                          # "momentum_breakout" or "__spy_buyhold__"
    display_name: str                      # "动量突破" or "SPY 基准"
    horizon: int                           # 5 / 20 / 60; 0 for SPY baseline

    # Trade counts
    n_trades: int
    n_capacity_skipped: int

    # Performance metrics (None if n_trades < 5)
    cumulative_return: float
    annual_return: float
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    calmar: float | None

    # Trade-level
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float

    # Equity curve (downsampled to ~120 points before return)
    daily_equity_curve: list[tuple[date, float]]

    # Benchmark: strategy.cumulative_return - spy.cumulative_return.
    # Populated by run_all_backtests() after the SPY baseline is computed —
    # direct callers of simulate_strategy_from_pairs() receive 0.0 here
    # (no SPY context available). SPY's own row reports 0.0 (baseline vs
    # itself). Units: decimal fraction (e.g. 0.03 = strategy beat SPY by
    # 3 percentage points of cumulative return over the window).
    excess_vs_spy: float

    # Defaulted (always-default in v0)
    mtm_model: str = "linear_interpolation_v0"

    # ---------- Reserved Phase 5 hooks (always None in v0) ----------
    # Phase 5 (True Portfolio Coupling) populates these for new runs;
    # v0 leaves them None for retroactive replay compatibility.
    strategy_exposure: float | None = None      # avg gross exposure during run
    capital_bid_score: float | None = None      # priority weight in shared pool
