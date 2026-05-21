# Layer: invariant


def test_always_approve_returns_approved_true():
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate
    from marketpulse.trading.types import AllocationRunId, OrderRequest

    req = OrderRequest(
        strategy="s", ticker="AAPL", quantity=10,
        event_time=datetime(2026, 5, 21, 14, 30, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150"), horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v0", allocator_version="v0",
        execution_engine_version="v0",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60, rewarded_for_negative_corr=False,
        would_change_rank=False, size_clamped_by_override=False,
    )

    gate = AlwaysApproveRiskGate()
    result = gate.check_pre_trade(order_request=req)
    assert result.approved is True
    assert result.reason == ""
