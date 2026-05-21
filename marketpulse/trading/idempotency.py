"""Deterministic idempotency-key computation (lock xvii, lock xxx, 6a-L7).

The key is derived from (strategy, ticker, event_time, allocation_run_id).
It does NOT depend on version fields — same-day rerun after a code deploy
is STILL replay, not recomputation (6a-L7)."""

from __future__ import annotations

import hashlib

from marketpulse.trading.types import OrderRequest


def compute_idempotency_key(order_request: OrderRequest) -> str:
    """Deterministic 16-char hex digest. Matches the DB UNIQUE column on
    paper_order.idempotency_key."""
    payload = "|".join([
        order_request.strategy,
        order_request.ticker,
        order_request.event_time.isoformat(),
        order_request.allocation_run_id,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
