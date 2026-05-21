"""Phase 5e: System policy constants — Layer: invariant tests for policy module."""
from __future__ import annotations


def test_min_overlap_days_anchored_at_30() -> None:
    """# Layer: invariant
    Anchors the MIN_OVERLAP_DAYS constant. Spec § 2 lock #7 fixes this value at 30.
    Any future bump requires conscious update of this test.
    """
    from marketpulse.backtest.policy import MIN_OVERLAP_DAYS
    assert MIN_OVERLAP_DAYS == 30
    assert isinstance(MIN_OVERLAP_DAYS, int)


def test_pool_corr_mode_anchored_at_loo_only_v0() -> None:
    """# Layer: invariant
    Anchors the POOL_CORR_MODE constant. Spec § 2 lock #7 + #21:
    v0 hardcodes LOO_ONLY; future variants would bump to LOO_OR_CF_v1.
    The constant is documentary-only — nothing branches on it at runtime.
    Any rename / bump requires conscious update of this test.
    """
    from marketpulse.backtest.policy import POOL_CORR_MODE
    assert POOL_CORR_MODE == "LOO_ONLY_v0"


def test_observability_mode_anchored_at_v1() -> None:
    """# Layer: invariant
    Spec § 2 lock #17. Anchors the OBSERVABILITY_MODE constant at "v1".
    Future schema bumps (v2 adds more fields to StrategyContribution)
    require conscious update of this test.
    """
    from marketpulse.backtest.policy import OBSERVABILITY_MODE
    assert OBSERVABILITY_MODE == "v1"
