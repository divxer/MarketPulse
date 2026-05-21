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

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np


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


def pool_corr_excluding_self(
    strategy_contribution_returns: list[tuple[date, float]],
    daily_pool_returns: list[tuple[date, float]],
    *,
    as_of: date,
    lookback_days: int = 60,
    min_overlap: int = 30,
) -> tuple[float | None, int]:
    """Pearson correlation between strategy's contribution returns and
    (pool_total − strategy_contribution) — leave-one-out via subtraction.

    Window: [as_of − lookback_days, as_of), exclusive upper bound.

    Returns (corr, effective_window):
      - corr = None when overlap < min_overlap (effective_window = actual overlap count, NOT 0)
      - corr = None when either series has zero variance (std == 0)
      - corr = None when computed corr is non-finite (defensive)
      - effective_window is always the actual overlap count, capped at lookback_days

    Semantic boundary (spec Appendix A): measures realized co-movement
    under competitive allocation constraints, NOT independent return
    correlation. The subtraction recovers an exact day-level decomposition
    of the realized pool, not a counterfactual A-less pool.
    """
    window_start = as_of - timedelta(days=lookback_days)
    strat_by_date = {
        d: v for d, v in strategy_contribution_returns
        if window_start <= d < as_of
    }
    pool_by_date = {
        d: v for d, v in daily_pool_returns
        if window_start <= d < as_of
    }
    overlap_dates = sorted(set(strat_by_date) & set(pool_by_date))
    overlap_count = len(overlap_dates)
    effective_window = min(overlap_count, lookback_days)

    if overlap_count < min_overlap:
        return None, effective_window

    strat_arr = np.array([strat_by_date[d] for d in overlap_dates], dtype=float)
    pool_arr = np.array([pool_by_date[d] for d in overlap_dates], dtype=float)
    # Leave-one-out via subtraction
    pool_minus_self_arr = pool_arr - strat_arr

    if strat_arr.std() < 1e-12 or pool_minus_self_arr.std() < 1e-12:
        return None, effective_window

    corr = float(np.corrcoef(strat_arr, pool_minus_self_arr)[0, 1])
    if not math.isfinite(corr):
        return None, effective_window
    return corr, effective_window


def compute_adjusted_bid_weight(
    raw_sharpe: float | None,
    pool_corr: float | None,
    *,
    lam: float = 0.5,
    clip_min: float = 0.5,
    clip_max: float = 1.2,
) -> tuple[float | None, float, bool]:
    """Apply contribution-adjusted multiplier to a raw bid weight.

    Returns (adjusted_weight, multiplier, rewarded_for_negative_corr):
      - adjusted = raw_sharpe × multiplier
      - multiplier = clip(1 − lam × pool_corr, clip_min, clip_max) when
        pool_corr is not None AND raw_sharpe > 0; else 1.0
      - rewarded = (pool_corr is not None) AND (pool_corr < 0) AND
        (multiplier > 1.0)

    Short-circuits to (None, 1.0, False) when raw_sharpe is None (Phase 5a
    n<5 floor — there is nothing to adjust).

    Short-circuits to (raw_sharpe, 1.0, False) when raw_sharpe <= 0
    (negative or zero — Phase 5a floor decides whether to bid; we don't
    amplify or attenuate).

    Short-circuits to (raw_sharpe, 1.0, False) when pool_corr is None
    (cold-start; failsafe-open per spec § 2 lock #4).

    Clip is asymmetric: [0.5, 1.2] is deliberate risk-aversion bias.
    Max penalty -50%, max reward +20%. v0 ships conservative form;
    a neutral clip would version-bump contribution_policy to _v1.
    """
    if raw_sharpe is None:
        return None, 1.0, False
    if raw_sharpe <= 0.0:
        return raw_sharpe, 1.0, False
    if pool_corr is None:
        return raw_sharpe, 1.0, False

    raw_multiplier = 1.0 - lam * pool_corr
    multiplier = max(clip_min, min(clip_max, raw_multiplier))
    adjusted = raw_sharpe * multiplier
    rewarded = (pool_corr < 0) and (multiplier > 1.0)
    return adjusted, multiplier, rewarded
