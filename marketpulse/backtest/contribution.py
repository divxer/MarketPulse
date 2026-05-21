"""Phase 5d-1: contribution-adjusted bid weight machinery.

Spec § 3 + § 4. Three pure public functions:
  - daily_contribution_return: per-day decomposition for LOO subtraction
  - pool_corr_excluding_self: Pearson on (strategy, pool_minus_self) returns
  - compute_adjusted_bid_weight: clip(1 − λρ, 0.5, 1.2) × raw_sharpe

Plus the BidWeightMetadata frozen dataclass that wraps per-strategy
per-day inputs to BidRecord construction.

ρ semantic boundary (spec Appendix A): pool_corr measures realized
co-movement under competitive allocation constraints, NOT independent
return correlation. Equivalent reads: "equilibrium decomposition
correlation", "structural decision-sensitivity input".
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BidWeightMetadata:
    """Per-strategy per-day inputs to BidRecord Phase 5d telemetry fields.

    Populated once per (strategy, day) in the WEIGHT step. Read at every
    BidRecord constructor site (7 sites across all outcome literals).
    Frozen for hashability + ergonomics (dataclasses.replace for flag updates).
    """
    raw: float | None
    pool_corr: float | None
    multiplier: float
    adjusted: float | None
    effective_window: int
    rewarded_for_negative_corr: bool
    would_change_rank: bool


def daily_contribution_return(
    strategy_pnl_today: float,
    pool_equity_prev_day: float,
) -> float:
    """Per-day strategy contribution to pool return.

    Returns strategy_pnl_today / pool_equity_prev_day. Returns 0.0 when
    pool_equity_prev_day is zero or negative (avoids ZeroDivisionError;
    cold-start safe; future-leverage-safe).
    """
    if pool_equity_prev_day <= 0.0:
        return 0.0
    return strategy_pnl_today / pool_equity_prev_day
