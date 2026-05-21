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
