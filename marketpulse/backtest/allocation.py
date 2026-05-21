# marketpulse/backtest/allocation.py
"""Phase 5/Phase 6 shared per-day allocation kernel (6a-0).

This module owns the BID → SIZE → DEDUP → ALLOC kernel extracted from
Phase 5's simulate_shared_pool. It is a PURE function — no DB, no Clock,
no ExecutionEngine, no audit, no I/O. Inputs are explicit dataclasses;
outputs are explicit dataclasses.

Lock 6a-L1: CLOSE, MTM, RECORD, equity-curve update, contribution
decomposition, rolling-stats finalization remain in
marketpulse/backtest/portfolio_simulator.py. The 6a-0 contract is a
narrow extraction.

Lock 6a-L9: AllocationContext carries every input the allocator needs
as an explicit named field. No hidden today dependency, no env lookup,
no DB read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# Version stamped onto paper_order.allocator_version for replay
# determinism (lock xxviii). Bump when the kernel's behavior changes.
ALLOCATOR_VERSION = "phase6a-v1"
__version__ = ALLOCATOR_VERSION


@dataclass(frozen=True)
class BidCandidate:
    """A raw bid candidate from the day's event stream. Phase 5 builds
    these from historical event/outcome JOIN rows; Phase 6 BidAggregator
    builds them from today's evaluation_event rows."""
    strategy: str
    ticker: str
    event_time: datetime          # UTC-aware (lock xxix)
    event_price: float
    horizon_date: date
    horizon_price: float | None   # filled by daily_cycle via PriceProvider
                                  # if BidAggregator left it None
    strategy_version: str


@dataclass(frozen=True)
class PositionSnapshot:
    """Currently-OPEN position as seen by the allocator at decision time."""
    strategy: str
    ticker: str
    quantity: int
    entry_price: float
    sector: str | None
    open_since: date


@dataclass(frozen=True)
class SizingContext:
    """Per-strategy sizing knobs. Stable across days for a single
    simulate_shared_pool run; the orchestrator threads it through."""
    base_position_size: float
    min_position: float
    max_position: float
    sizing_enabled: bool
    per_strategy_overrides: dict[
        str, tuple[float | None, float | None, float | None]
    ]


@dataclass(frozen=True)
class AllocationContext:
    """Explicit allocation-decision inputs (6a-L9). Every field is
    required; no defaults that hide a today dependency."""
    allocation_date: date
    target_vol: float
    lookback_days: int
    sector_caps_enabled: bool
    sector_cap_pct: float
    correlation_caps_enabled: bool
    correlation_cap_pct: float
    correlation_threshold: float
    contribution_enabled: bool
    contribution_lambda: float
    pool_corr_mode: str
    phase5e_warm_pool_overlap_days: int
    max_capital_in_use: float


@dataclass(frozen=True)
class AllocationWinner:
    """A bid that survived sizing + dedup + caps. Threaded into Phase 6
    OrderRequest construction."""
    strategy: str
    ticker: str
    event_time: datetime          # UTC-aware (lock xxix)
    event_price: float
    horizon_date: date
    horizon_price: float | None
    quantity: int
    weight: float
    raw_bid_weight: float | None
    pool_corr: float | None
    contribution_multiplier: float
    adjusted_bid_weight: float | None
    effective_corr_window: int
    rewarded_for_negative_corr: bool
    would_change_rank: bool
    size_clamped_by_override: bool
    strategy_version: str


@dataclass(frozen=True)
class BlockedBidReason:
    """Reason a bid did not become a winner. Typed dataclass replaces
    the earlier 'tuple[object, ...]' to prevent architecture erosion."""
    strategy: str
    ticker: str
    reason: str          # one of: dedup_loser | cap_full | sector_cap_full
                         # | correlation_cap_full | cash_short | size_too_small
    weight: float | None
    raw_bid_weight: float | None
    pool_corr: float | None
    contribution_multiplier: float
    adjusted_bid_weight: float | None


@dataclass(frozen=True)
class AllocationResult:
    """Outcome of one per-day allocation call.

    Authority contract: the allocator is the CANONICAL source for
    cash_used / cash_remaining for this batch. Callers (daily_cycle,
    simulate_shared_pool) MUST NOT recompute these values from the
    winners list — that introduces derived-state duplication and silent
    drift. Read them as-is."""
    winners: tuple[AllocationWinner, ...]
    blocked: tuple[BlockedBidReason, ...]
    cash_used: float        # canonical — do not recompute
    cash_remaining: float   # canonical — do not recompute


def allocate_for_day(
    *,
    bids: list[BidCandidate],
    existing_positions: list[PositionSnapshot],
    cash_available: float,
    allocation_context: AllocationContext,
    sizing_context: SizingContext,
) -> AllocationResult:
    """Pure-function per-day allocation kernel.

    Stub for 6a-0.2. Real BID → SIZE → DEDUP → ALLOC logic is lifted out
    of simulate_shared_pool in 6a-0.4.
    """
    raise NotImplementedError(
        "allocate_for_day will be wired in 6a-0.4 (extraction step)"
    )
