# Layer: invariant
"""6a-1: compute_idempotency_key is deterministic over the lock-xvii inputs."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal


def _sample_request(strategy="s", ticker="AAPL", run_id="paper-2026-05-21"):
    from marketpulse.trading.types import AllocationRunId, OrderRequest

    return OrderRequest(
        strategy=strategy,
        ticker=ticker,
        quantity=10,
        event_time=datetime(2026, 5, 21, 14, 30, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150.00"),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155.00"),
        allocation_run_id=AllocationRunId(run_id),
        strategy_version="v0",
        allocator_version="v0",
        execution_engine_version="v0",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60, rewarded_for_negative_corr=False,
        would_change_rank=False, size_clamped_by_override=False,
    )


def test_idempotency_key_is_deterministic():
    from marketpulse.trading.idempotency import compute_idempotency_key

    k1 = compute_idempotency_key(_sample_request())
    k2 = compute_idempotency_key(_sample_request())
    assert k1 == k2


def test_idempotency_key_distinguishes_strategy():
    from marketpulse.trading.idempotency import compute_idempotency_key

    k1 = compute_idempotency_key(_sample_request(strategy="a"))
    k2 = compute_idempotency_key(_sample_request(strategy="b"))
    assert k1 != k2


def test_idempotency_key_distinguishes_ticker():
    from marketpulse.trading.idempotency import compute_idempotency_key

    k1 = compute_idempotency_key(_sample_request(ticker="AAPL"))
    k2 = compute_idempotency_key(_sample_request(ticker="MSFT"))
    assert k1 != k2


def test_idempotency_key_distinguishes_run_id():
    from marketpulse.trading.idempotency import compute_idempotency_key

    k1 = compute_idempotency_key(_sample_request(run_id="paper-2026-05-21"))
    k2 = compute_idempotency_key(_sample_request(run_id="paper-2026-05-22"))
    assert k1 != k2


def test_idempotency_key_independent_of_version_fields():
    """6a-L7: same-day rerun after code deploy is STILL replay. The key
    must NOT include allocator_version or execution_engine_version."""
    from marketpulse.trading.idempotency import compute_idempotency_key
    from marketpulse.trading.types import OrderRequest

    base = _sample_request()
    bumped = OrderRequest(
        **{**base.__dict__, "allocator_version": "v999",
           "execution_engine_version": "v999"}
    )
    assert compute_idempotency_key(base) == compute_idempotency_key(bumped)
