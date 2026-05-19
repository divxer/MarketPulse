"""Backtest Engine MVP (Phase 4) — Strategy Performance Observatory.

A reproducible research observatory for strategy-level synthetic PnL
analysis under constrained-capital simulation assumptions. NOT a
faithful execution-level trading simulator.

See docs/superpowers/specs/2026-05-19-phase-4-backtest-engine-mvp.md
for the locked design decisions (16 of them).
"""
from marketpulse.backtest.types import StrategyBacktestResult

__all__ = ["StrategyBacktestResult"]
