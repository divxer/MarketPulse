# Layer: pure
"""6b-T1..T6: RiskIntent + RiskConfigProvider tests."""

from __future__ import annotations


def test_risk_intent_enum_values():
    from marketpulse.trading.types import RiskIntent
    assert RiskIntent.OPEN == "open"
    assert RiskIntent.ADD == "add"
    assert RiskIntent.CLOSE == "close"
    assert RiskIntent.REDUCE == "reduce"
    assert RiskIntent.FLIP == "flip"


def test_risk_intent_is_str_enum():
    from marketpulse.trading.types import RiskIntent
    # StrEnum membership preserves str identity
    assert isinstance(RiskIntent.OPEN, str)


def test_order_request_defaults_risk_intent_to_open():
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent

    req = OrderRequest(
        strategy="momentum_breakout",
        ticker="AAPL",
        quantity=10,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150.00"),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155.00"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v1",
        allocator_version="phase6a-v1",
        execution_engine_version="phase6a-v1",
        weight=1.0,
        raw_bid_weight=1.0,
        pool_corr=0.1,
        contribution_multiplier=1.0,
        adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
    )
    assert req.risk_intent == RiskIntent.OPEN
