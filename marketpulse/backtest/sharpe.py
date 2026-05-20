"""Rolling causal Sharpe service for Phase 5a bid weighting.

Spec § 3: bid weights come from rolling Sharpe computed on the
per-strategy ISOLATED daily curve (Phase 4 output), NOT the
shared-pool slice. This is an intentional bootstrap that decouples
weight measurement from realized PnL to avoid recursive starvation.

Causality: rolling_sharpe(curve, as_of, lookback_days) returns the
Sharpe of curve values dated in [as_of - lookback_days, as_of).
Outcomes with date >= as_of are excluded (no future leakage).
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
from empyrical import sharpe_ratio


def rolling_sharpe(
    daily_curve: list[tuple[date, float]],
    *,
    as_of: date,
    lookback_days: int = 60,
    min_events: int = 5,
) -> float | None:
    """Sharpe of daily-return diffs of curve points in [as_of - lookback, as_of).

    Returns None when:
      - Fewer than `min_events` qualifying points (matches Phase 4 n<5 floor).
      - empyrical returns inf/-inf (degenerate zero-variance input).
    """
    if not daily_curve:
        return None
    window_start = as_of - timedelta(days=lookback_days)
    sliced = [(d, v) for d, v in daily_curve if window_start <= d < as_of]
    if len(sliced) < min_events:
        return None
    values = np.array([v for _, v in sliced], dtype=float)
    if len(values) < 2:
        return None
    daily_returns = np.diff(values) / values[:-1]
    s = float(sharpe_ratio(daily_returns))
    if not math.isfinite(s):
        return None
    return s


def rolling_sigma(
    daily_curve: list[tuple[date, float]],
    *,
    as_of: date,
    lookback_days: int = 60,
    min_events: int = 5,
) -> float | None:
    """Daily-return σ over curve points in [as_of - lookback, as_of).

    Returns None when:
      - Fewer than `min_events` qualifying points
      - σ computes to exactly 0 (degenerate zero-variance, e.g., flat curve)
      - Non-finite result (shouldn't happen with np.std on finite input)

    Causality: identical window semantics to rolling_sharpe.
    """
    if not daily_curve:
        return None
    window_start = as_of - timedelta(days=lookback_days)
    sliced = [(d, v) for d, v in daily_curve if window_start <= d < as_of]
    if len(sliced) < min_events:
        return None
    values = np.array([v for _, v in sliced], dtype=float)
    if len(values) < 2:
        return None
    daily_returns = np.diff(values) / values[:-1]
    s = float(np.std(daily_returns))
    if not math.isfinite(s) or s == 0.0:
        return None
    return s


def rolling_alpha(
    daily_curve: list[tuple[date, float]],
    *,
    as_of: date,
    lookback_days: int = 60,
    min_events: int = 5,
) -> float | None:
    """Daily-return MEAN (alpha) over curve points in [as_of - lookback, as_of).

    Returns None when:
      - Fewer than `min_events` qualifying points
      - Non-finite mean (shouldn't happen with np.mean on finite input)

    Used by Phase 5b's compute_position_sizes as the conviction signal.
    Distinct from rolling_sharpe — alpha is raw mean return WITHOUT division
    by σ. Using alpha (not Sharpe) for sizing conviction avoids the μ/σ²
    double-count described in spec § 1.

    Causality: identical window semantics to rolling_sigma.
    """
    if not daily_curve:
        return None
    window_start = as_of - timedelta(days=lookback_days)
    sliced = [(d, v) for d, v in daily_curve if window_start <= d < as_of]
    if len(sliced) < min_events:
        return None
    values = np.array([v for _, v in sliced], dtype=float)
    if len(values) < 2:
        return None
    daily_returns = np.diff(values) / values[:-1]
    a = float(np.mean(daily_returns))
    if not math.isfinite(a):
        return None
    return a


def compute_bid_weights(
    strategies_today: list[str],
    daily_curves: dict[str, list[tuple[date, float]]],
    *,
    as_of: date,
    lookback_days: int = 60,
    min_floor: float = 0.1,
    min_events: int = 5,
) -> tuple[dict[str, float], set[str]]:
    """Compute per-strategy bid weights using rolling Sharpe.

    Returns (weights, floor_hits):
      - weights: dict[strategy_name, float] — final weight (post-floor, post-bootstrap)
      - floor_hits: set[strategy] whose raw Sharpe was below min_floor and
        was clipped up. Used by the simulator for n_floor_hits telemetry.

    Algorithm (spec § 3):
      1. rolling_sharpe per strategy on its slice of daily_curves.
      2. If all None → all weights = 1.0 (full equal-weight bootstrap).
      3. Otherwise: None strategies get avg of known weights; floor at min_floor.

    Contract:
      - Every entry of `strategies_today` MUST be a key of `daily_curves`.
        Raises KeyError on missing.
      - Empty curve for a strategy → its rolling_sharpe is None → bootstrap path.
    """
    raw: dict[str, float | None] = {
        s: rolling_sharpe(
            daily_curves[s], as_of=as_of, lookback_days=lookback_days,
            min_events=min_events,
        )
        for s in strategies_today
    }

    known = [w for w in raw.values() if w is not None]
    if not known:
        return {s: 1.0 for s in raw}, set()

    avg_known = sum(known) / len(known)
    weights: dict[str, float] = {}
    floor_hits: set[str] = set()
    for s, w in raw.items():
        unfloored = w if w is not None else avg_known
        if unfloored < min_floor:
            weights[s] = min_floor
            floor_hits.add(s)
        else:
            weights[s] = unfloored
    return weights, floor_hits


def compute_position_sizes(
    strategies_today: list[str],
    daily_curves: dict[str, list[tuple[date, float]]],
    *,
    as_of: date,
    base: float = 1_000.0,
    target_vol: float = 0.01,
    min_position: float = 200.0,
    max_position: float = 4_000.0,
    lookback_days: int = 60,
    min_events: int = 5,
) -> tuple[dict[str, float | None], dict[str, float]]:
    """Compute per-strategy position size (Hybrid: vol-target × alpha-conviction).

    Spec § 1 ⚠ box: NOT bid_weights (Sharpe) as conviction signal — would
    double-count σ. Uses rolling_alpha (raw mean return) instead.

    Returns (sizes, raw_sizes_below_min):
      - sizes: dict[strategy, float | None]. None means raw < min_position;
        caller skips with outcome=size_too_small.
      - raw_sizes_below_min: dict[strategy, float] capturing RAW pre-clamp
        size for each None-returning strategy. Caller logs this on the
        size_too_small BidRecord so the bid history shows "model wanted $42"
        not just "blocked".

    Algorithm:
      1. For each s, compute σ_s and α_s via rolling_sigma / rolling_alpha.
      2. mean_α = mean over strategies where α is not None.
         - If ALL α are None → mean_α = None → all α_scale = 1.0 (full bootstrap).
         - If SOME α None → those get α_scale = 1.0; others use α_s/mean_α.
      3. For each s:
         vol_scale = target_vol / σ_s   if σ_s is not None and σ_s > 0
                     else 1.0
         α_scale   = α_s / mean_α       if α_s is not None and mean_α is not None
                     else 1.0
         raw = base * vol_scale * α_scale
         if raw < min_position:
             sizes[s] = None; raw_sizes_below_min[s] = raw
         else:
             sizes[s] = min(raw, max_position)  # ceiling clamp only
      4. Return (sizes, raw_sizes_below_min).

    Contract:
      - Every entry of strategies_today MUST appear in daily_curves.
        Raises KeyError on missing.
    """
    sigmas: dict[str, float | None] = {
        s: rolling_sigma(daily_curves[s], as_of=as_of,
                         lookback_days=lookback_days, min_events=min_events)
        for s in strategies_today
    }
    alphas: dict[str, float | None] = {
        s: rolling_alpha(daily_curves[s], as_of=as_of,
                         lookback_days=lookback_days, min_events=min_events)
        for s in strategies_today
    }
    known_alphas = [a for a in alphas.values() if a is not None]
    mean_alpha: float | None = (
        sum(known_alphas) / len(known_alphas) if known_alphas else None
    )

    sizes: dict[str, float | None] = {}
    raw_below: dict[str, float] = {}
    for s in strategies_today:
        sigma = sigmas[s]
        alpha = alphas[s]
        vol_scale = target_vol / sigma if (sigma is not None and sigma > 0) else 1.0
        if alpha is not None and mean_alpha is not None and mean_alpha != 0:
            alpha_scale = alpha / mean_alpha
        else:
            alpha_scale = 1.0
        raw = base * vol_scale * alpha_scale
        if raw < min_position:
            sizes[s] = None
            raw_below[s] = raw
        else:
            sizes[s] = min(raw, max_position)
    return sizes, raw_below
