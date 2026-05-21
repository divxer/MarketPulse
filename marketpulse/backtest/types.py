"""StrategyBacktestResult — frozen dataclass returned by the simulator."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


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


@dataclass(frozen=True)
class StrategyBacktestArtifacts:
    """Diagnostic + cross-module compute layer for a per-strategy run.

    Separates SERIALIZATION concerns (StrategyBacktestResult — what goes
    into templates, JSON, pickle, API responses) from COMPUTE concerns
    (StrategyBacktestArtifacts — what the Phase 5a shared-pool simulator
    needs internally for rolling Sharpe lookups).
    """
    strategy: str
    full_equity_curve: list[tuple[date, float]]


@dataclass(frozen=True)
class StrategyContribution:
    """One strategy's slice of a shared-pool run."""
    strategy: str
    display_name: str
    n_trades: int
    n_dedup_skipped: int
    n_capacity_skipped: int
    n_cash_short_skipped: int
    n_size_too_small_skipped: int   # NEW Phase 5b
    n_sector_cap_skipped: int       # NEW Phase 5c-1
    n_correlation_cap_skipped: int  # NEW Phase 5c-2
    contribution_pnl: float
    avg_exposure: float
    avg_bid_weight: float
    avg_position_size: float       # NEW Phase 5b
    n_bids: int
    n_floor_hits: int

    # NEW Phase 5d (both defaulted)
    avg_pool_corr: float | None = None
    n_would_change_rank: int = 0


@dataclass(frozen=True)
class BidRecord:
    """One bid decision — diagnostic timeline."""
    date: date
    strategy: str
    ticker: str
    weight: float
    outcome: Literal[
        "won", "dedup_loser", "cap_full", "cash_short",
        "size_too_small",          # NEW Phase 5b
        "sector_cap_full",         # NEW Phase 5c-1
        "correlation_cap_full",    # NEW Phase 5c-2
    ]
    winner: str | None
    position_size: float  # NEW Phase 5b — model's REQUESTED size in dollars.
                          # Preserves diagnostic value across all outcomes:
                          #   won:           actual opened size (post-clamp)
                          #   dedup_loser:   what this strategy would have opened
                          #   cap_full:      what was requested but cap-blocked
                          #   cash_short:    what was requested but cash-blocked
                          #   size_too_small: raw pre-clamp size (e.g. $42)
                          # See spec § 3 for the rationale.
    # NEW Phase 5c diagnostic fields (default empty; populated only for the
    # matching outcome). Frozen-dataclass-safe — string and tuple are hashable.
    blocked_by_sector: str | None = None
    blocked_by_correlation_with: tuple[tuple[str, float], ...] = ()
    # NEW Phase 5d (all defaulted for backward-compat with existing fixtures)
    raw_bid_weight: float | None = None
    pool_corr: float | None = None
    contribution_multiplier: float = 1.0
    adjusted_bid_weight: float | None = None
    effective_corr_window: int = 0
    rewarded_for_negative_corr: bool = False
    would_change_rank: bool = False
    # NEW Phase 5e (lock #23) — clamp-attribution flag. True iff the raw
    # sized magnitude exceeded the strategy's effective max ceiling for the
    # day (i.e., the eff_max clamp was binding). Independent of cap clamps
    # (cap_full, sector_cap_full, correlation_cap_full) which are separate
    # outcomes. Defaulted so existing BidRecord construction sites continue
    # to work; populated for ALL 7 outcome sites in portfolio_simulator.
    size_clamped_by_override: bool = False


@dataclass(frozen=True)
class PortfolioBacktestResult:
    """Phase 5a shared-pool result — the ONE portfolio combining all strategies."""

    # Identity (required)
    horizon: int

    # Aggregate counts (required)
    n_trades: int
    n_dedup_total: int

    # Utilization (required)
    avg_capital_utilization: float

    # NEW Phase 5b concentration telemetry (required) —
    # observation-only in v0; Phase 5d will enforce risk budgets using these.
    max_strategy_exposure: float
    hhi_concentration: float

    # NEW Phase 5c-1 sector telemetry (required)
    max_sector_exposure: float
    max_sector_exposure_by_sector: dict[str, float]
    sector_breakdown: dict[str, float]

    # NEW Phase 5c-2 correlation telemetry (required)
    max_neighbor_exposure: float
    n_correlation_cap_events: int

    # Performance metrics (required; sharpe/sortino/calmar may be None)
    cumulative_return: float
    annual_return: float
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    calmar: float | None
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float

    # Series + benchmarks (required)
    daily_equity_curve: list[tuple[date, float]]
    excess_vs_spy: float

    # Breakdown + diagnostics (required)
    per_strategy_stats: dict[str, StrategyContribution]
    bid_history: list[BidRecord]

    # Defaulted provenance (always-default in v0)
    display_name: str = "Shared Pool"
    mtm_model: str = "linear_interpolation_v0"
    bid_policy: str = "rolling_sharpe_60d_v0"
    sizing_policy: str = "fixed_v0"  # NEW Phase 5b
    sector_cap_policy: str = "uniform_40pct_v0"                  # NEW Phase 5c-1
    correlation_cap_policy: str = "neighbor_sum_rho06_40pct_v0"  # NEW Phase 5c-2
    sector_caps_enabled: bool = True                              # NEW Phase 5c-1
    correlation_caps_enabled: bool = True                         # NEW Phase 5c-2
    risk_policy: str = "cap40_corr06_enforced_v0"                 # NEW Phase 5c composite tag
    # NEW Phase 5d provenance
    contribution_enabled: bool = False
    contribution_policy: str = "contribution_adjusted_sharpe_60d_v0"
    contribution_lambda: float = 0.5
