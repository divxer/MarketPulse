"""Backtest Engine (Phase 4 + Phase 5a) — Strategy Performance Observatory.

A reproducible research observatory for strategy-level synthetic PnL
analysis under constrained-capital simulation assumptions. NOT a
faithful execution-level trading simulator.

Specs:
  docs/superpowers/specs/2026-05-19-phase-4-backtest-engine-mvp.md
  docs/superpowers/specs/2026-05-20-phase-5a-shared-capital-pool-design.md
"""
from marketpulse.backtest.simulator import (
    run_all_backtests,
    run_shared_pool_backtest,
    simulate_spy_buyhold,
    simulate_strategy_from_pairs,
    simulate_strategy_with_artifacts,
)
from marketpulse.backtest.types import (
    BidRecord,
    PortfolioBacktestResult,
    StrategyBacktestArtifacts,
    StrategyBacktestResult,
    StrategyContribution,
)

__all__ = [
    "BidRecord",
    "PortfolioBacktestResult",
    "StrategyBacktestArtifacts",
    "StrategyBacktestResult",
    "StrategyContribution",
    "run_all_backtests",
    "run_shared_pool_backtest",
    "simulate_spy_buyhold",
    "simulate_strategy_from_pairs",
    "simulate_strategy_with_artifacts",
]
