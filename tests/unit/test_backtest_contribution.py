"""Phase 5d: contribution.py — per-day decomposition + LOO correlation + adjusted bid weight."""
from __future__ import annotations


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
