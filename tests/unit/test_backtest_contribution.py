"""Phase 5d: contribution.py — per-day decomposition + LOO correlation + adjusted bid weight."""
from __future__ import annotations

from datetime import date, timedelta


def test_daily_contribution_return_basic() -> None:
    """pnl=100, equity_prev=10000 → 0.01."""
    from marketpulse.backtest.contribution import daily_contribution_return
    assert daily_contribution_return(100.0, 10_000.0) == 0.01


def test_daily_contribution_return_zero_equity_prev_returns_zero() -> None:
    """pnl=100, equity_prev=0 → 0.0 (no ZeroDivisionError)."""
    from marketpulse.backtest.contribution import daily_contribution_return
    assert daily_contribution_return(100.0, 0.0) == 0.0


def test_daily_contribution_return_negative_equity_prev_returns_zero() -> None:
    """Forward-compat: pnl=100, equity_prev=-5000 → 0.0.

    Phase 4 forbids negative equity; future leverage may allow it.
    """
    from marketpulse.backtest.contribution import daily_contribution_return
    assert daily_contribution_return(100.0, -5000.0) == 0.0


def test_bid_weight_metadata_is_frozen_dataclass() -> None:
    """BidWeightMetadata is a frozen dataclass (hashable, immutable)."""
    import dataclasses

    from marketpulse.backtest.contribution import BidWeightMetadata
    meta = BidWeightMetadata(
        raw=1.5, pool_corr=0.3, multiplier=0.85,
        adjusted=1.275, effective_window=42,
        rewarded_for_negative_corr=False, would_change_rank=False,
    )
    assert dataclasses.is_dataclass(meta)
    assert hash(meta)  # hashable means frozen
    # dataclasses.replace works
    new_meta = dataclasses.replace(meta, would_change_rank=True)
    assert new_meta.would_change_rank is True
    assert meta.would_change_rank is False  # original unchanged


def _build_returns(start_date: date, values: list[float]) -> list[tuple[date, float]]:
    """Helper: build (date, value) tuples starting at start_date, one per day."""
    return [(start_date + timedelta(days=i), v) for i, v in enumerate(values)]


def test_pool_corr_excluding_self_perfectly_correlated() -> None:
    """A_returns identical to (pool − A) returns → ρ ≈ 1.0."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    start = date(2026, 1, 1)
    # A contributes consistently; rest of pool moves identically
    strategy_returns = _build_returns(start, [0.01, -0.005, 0.02, 0.008, -0.012] * 7)  # 35 days
    # pool_total = strategy_contribution + identical_rest → pool_minus_A = identical_rest = A
    pool_returns = _build_returns(start, [0.02, -0.01, 0.04, 0.016, -0.024] * 7)  # 2× strategy

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
    )
    assert corr is not None
    assert corr > 0.99
    assert eff >= 30


def test_pool_corr_excluding_self_anti_correlated() -> None:
    """A_returns = −(pool − A) returns → ρ ≈ −1.0."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    start = date(2026, 1, 1)
    strategy_returns = _build_returns(start, [0.01, -0.005, 0.02, 0.008, -0.012] * 7)
    # pool_total = 0 (A cancels rest exactly), so pool_minus_A = -A
    pool_returns = _build_returns(start, [0.0] * 35)

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
    )
    assert corr is not None
    assert corr < -0.99
    assert eff >= 30


def test_pool_corr_excluding_self_cold_start_returns_count() -> None:
    """Below min_overlap → (None, actual_overlap_count). NOT (None, 0)."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    start = date(2026, 2, 25)  # only 10 days before as_of
    strategy_returns = _build_returns(start, [0.01] * 10)
    pool_returns = _build_returns(start, [0.02] * 10)

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 7),
        lookback_days=60,
        min_overlap=30,
    )
    assert corr is None
    # Informative telemetry: how many overlap days actually existed
    assert eff == 10


def test_pool_corr_excluding_self_empty_intersection_returns_zero() -> None:
    """No overlap at all → (None, 0)."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    # as_of before any returns exist
    strategy_returns = _build_returns(date(2026, 6, 1), [0.01] * 10)
    pool_returns = _build_returns(date(2026, 6, 1), [0.02] * 10)

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 1),  # before any returns
        lookback_days=60,
        min_overlap=30,
    )
    assert corr is None
    assert eff == 0


def test_pool_corr_excluding_self_partial_overlap_uses_actual_window() -> None:
    """30 ≤ overlap < 60 → corr computed on actual overlap, eff = actual."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    # 45 days of overlap available
    start = date(2026, 1, 20)
    strategy_returns = _build_returns(start, [0.01, -0.005] * 23)  # 46 days
    pool_returns = _build_returns(start, [0.02, -0.01] * 23)

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
    )
    # Precondition: actual overlap should be ~44 (start + 46 - days past as_of)
    # Outcome: corr defined, eff between min_overlap and lookback_days
    assert corr is not None
    assert 30 <= eff < 60


def test_pool_corr_excluding_self_zero_variance_strategy_returns_none() -> None:
    """A_returns all zero → std=0 → (None, overlap_count)."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    start = date(2026, 1, 1)
    strategy_returns = _build_returns(start, [0.0] * 35)
    pool_returns = _build_returns(start, [0.02, -0.01] * 17 + [0.01])  # 35 days

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
    )
    assert corr is None
    # Precondition: enough overlap existed even though variance was zero
    assert eff >= 30


def test_pool_corr_excluding_self_zero_variance_pool_minus_a_returns_none() -> None:
    """pool_minus_A all zero → std=0 → (None, overlap_count)."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    start = date(2026, 1, 1)
    # A is the only contributor; rest of pool is flat (pool_minus_A all zero)
    strategy_returns = _build_returns(start, [0.01, -0.005] * 17 + [0.01])
    pool_returns = strategy_returns  # pool_total == A → pool_minus_A == 0

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
    )
    assert corr is None
    assert eff >= 30


def test_pool_corr_excluding_self_excludes_dates_at_or_after_as_of() -> None:
    """Window is [as_of − lookback, as_of) — exclusive upper bound."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    start = date(2026, 1, 1)
    # 60 days before as_of + 30 days after
    strategy_returns = _build_returns(start, [0.01, -0.01] * 45)  # 90 days
    pool_returns = _build_returns(start, [0.02, -0.02] * 45)

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 2),  # ~60 days after start
        lookback_days=60,
        min_overlap=30,
    )
    # Should NOT include days >= as_of in the correlation
    assert corr is not None
    assert eff <= 60  # capped at lookback_days
