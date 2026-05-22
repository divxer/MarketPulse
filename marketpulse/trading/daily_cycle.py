"""Phase 6a daily orchestration (lock xxv: scheduler is thin; this owns
the real sequence).

Sequence:
    1. Gap detection (forward-only — lock xxxiii)
    1.5. Kill switch cycle-level short-circuit (6a-L8)
    2. Collect today's bids
    3. allocate_for_day(...)
    4. place_order × N (horizon_price filled via PriceProvider before
       OrderRequest construction — lock R6-15)
    5. tick(as_of=tick_date)
    6. TICK_COMPLETED (or TICK_REPROCESSED_COMPLETED) audit
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from marketpulse.backtest.allocation import (
    ALLOCATOR_VERSION,
    AllocationContext,
    AllocationResult,
    PositionSnapshot,
    SizingContext,
)
from marketpulse.trading.bid_aggregator import BidAggregator
from marketpulse.trading.calendar import NYTradingCalendar
from marketpulse.trading.clock import Clock
from marketpulse.trading.execution_engine import ExecutionEngine
from marketpulse.trading.forward_engine import EXECUTION_ENGINE_VERSION
from marketpulse.trading.kill_switch import KillSwitchState
from marketpulse.trading.price_provider import PriceProvider
from marketpulse.trading.repository import Repository
from marketpulse.trading.types import (
    AllocationRunId,
    AuditEventType,
    OrderRejected,
    OrderRequest,
    TickError,
)


@dataclass(frozen=True)
class DailyCycleResult:
    tick_date: date
    allocation_run_id: AllocationRunId
    bids_collected: int
    orders_placed: int
    orders_rejected: int
    duplicates_skipped: int
    entries_materialized: int
    exits_materialized: int
    tick_errors: tuple[TickError, ...]
    cycle_status: Literal[
        "completed", "completed_with_errors", "kill_switch_skipped",
    ]
    cash_balance_end: Decimal


def _make_order_request(
    *,
    winner,
    allocation_run_id: AllocationRunId,
    allocation_date: date,
    price_provider: PriceProvider,
) -> OrderRequest:
    """Quantization site: float → Decimal at the OrderRequest boundary
    (lock xxii).

    T3 shim (lock 6b+L1): forward mode ALWAYS leaves horizon_price=None.
    Even if a winner arrives with a pre-filled horizon_price (Phase 5
    backtest convention leaking through the forward path — which
    shouldn't happen but defend against drift), we ignore it. T8 will
    delete this whole shim and the price_provider kwarg."""
    horizon_price = None
    return OrderRequest(
        strategy=winner.strategy,
        ticker=winner.ticker,
        quantity=winner.quantity,
        event_time=winner.event_time,
        allocation_date=allocation_date,
        event_price=Decimal(str(winner.event_price)),
        horizon_date=winner.horizon_date,
        horizon_price=(
            Decimal(str(horizon_price)) if horizon_price is not None else None
        ),
        allocation_run_id=allocation_run_id,
        strategy_version=winner.strategy_version,
        allocator_version=ALLOCATOR_VERSION,
        execution_engine_version=EXECUTION_ENGINE_VERSION,
        weight=winner.weight,
        raw_bid_weight=winner.raw_bid_weight,
        pool_corr=winner.pool_corr,
        contribution_multiplier=winner.contribution_multiplier,
        adjusted_bid_weight=winner.adjusted_bid_weight,
        effective_corr_window=winner.effective_corr_window,
        rewarded_for_negative_corr=winner.rewarded_for_negative_corr,
        would_change_rank=winner.would_change_rank,
        size_clamped_by_override=winner.size_clamped_by_override,
    )


def _position_snapshots(repo: Repository) -> list[PositionSnapshot]:
    """Translate paper_position rows into PositionSnapshot dataclasses
    for the pure allocator kernel."""
    return [
        PositionSnapshot(
            strategy=p.strategy, ticker=p.ticker, quantity=p.quantity,
            entry_price=float(p.entry_price), sector=None,
            open_since=p.entry_date,
        )
        for p in repo.open_positions_snapshot()
    ]


def run(
    *,
    clock: Clock,
    engine: ExecutionEngine,
    repository: Repository,
    bid_aggregator: BidAggregator,
    allocator: Callable[..., AllocationResult],
    calendar: NYTradingCalendar,
    kill_switch: KillSwitchState,
    price_provider: PriceProvider,
    # Allocator-kernel context. Phase 6 forward mode passes empty
    # curves + real get_sector (paper-trading history isn't accumulated
    # for allocator weight inputs yet — that's 6b/6c work). The kernel
    # falls back to base_position_size when curves are empty.
    daily_curves: dict[str, list[tuple[date, float]]] | None = None,
    daily_strategy_contribution_returns: dict[
        str, list[tuple[date, float]]
    ] | None = None,
    daily_pool_returns: list[tuple[date, float]] | None = None,
    sector_provider: Callable[[str], str] | None = None,
) -> DailyCycleResult:
    tick_date = calendar.today_ny_trading_date(clock.now())
    allocation_run_id = AllocationRunId(f"paper-{tick_date.isoformat()}")

    # === Phase 1: gap detection ===
    # sessions_after returns sessions in (last_processed, tick_date].
    # That count includes tick_date itself; subtract 1 to get strictly
    # missed sessions BETWEEN them. Round-6 fix: explicit primitive
    # avoids the inclusive/exclusive ambiguity.
    last_processed = repository.last_processed_tick_date()
    if last_processed is not None and last_processed < tick_date:
        missed = calendar.sessions_after(last_processed, tick_date) - 1
        if missed > 0:
            with repository.transaction():
                repository.write_gap_audit_once(
                    last_tick=last_processed,
                    resume_date=tick_date,
                    missed_business_days=missed,
                    timestamp=clock.now(),
                )

    # === Phase 1.5: kill-switch cycle-level short-circuit (6a-L8) ===
    if kill_switch.is_active():
        tick_result = engine.tick(as_of=tick_date)
        with repository.transaction():
            repository.write_audit_event(
                event_type=AuditEventType.KILL_SWITCH_CYCLE_SKIPPED,
                order_id=None,
                strategy=None,
                reason="kill_switch_active",
                context={
                    "tick_date": tick_date.isoformat(),
                    "mode": "kill_switch_active",
                    "tick_entries_materialized": tick_result.entries_materialized,
                    "tick_exits_materialized": tick_result.exits_materialized,
                    "tick_errors": [
                        {
                            "phase": e.phase,
                            "order_id": e.order_id,
                            "position_id": e.position_id,
                            "error": e.error,
                        }
                        for e in tick_result.errors
                    ],
                },
                timestamp=clock.now(),
            )
        return DailyCycleResult(
            tick_date=tick_date,
            allocation_run_id=allocation_run_id,
            bids_collected=0,
            orders_placed=0,
            orders_rejected=0,
            duplicates_skipped=0,
            entries_materialized=tick_result.entries_materialized,
            exits_materialized=tick_result.exits_materialized,
            tick_errors=tick_result.errors,
            cycle_status="kill_switch_skipped",
            cash_balance_end=repository.cash_balance(),
        )

    # === Phase 2: collect today's raw bids ===
    bids = bid_aggregator.collect_for_date(tick_date)

    # === Phase 3: allocate (pure) ===
    allocation_ctx = AllocationContext(
        allocation_date=tick_date,
        target_vol=0.01,
        lookback_days=60,
        sector_caps_enabled=True,
        sector_cap_pct=0.40,
        correlation_caps_enabled=True,
        correlation_cap_pct=0.40,
        correlation_threshold=0.60,
        contribution_enabled=False,
        contribution_lambda=0.5,
        pool_corr_mode="excludes_self",
        phase5e_warm_pool_overlap_days=20,
        max_capital_in_use=10_000.0,
    )
    sizing_ctx = SizingContext(
        base_position_size=1_000.0,
        min_position=200.0,
        max_position=4_000.0,
        sizing_enabled=True,
        per_strategy_overrides={},
    )
    # Forward-mode defaults (Phase 6 has no allocator-input history yet —
    # 6b/6c will wire real curves/returns). Empty containers cause the
    # kernel's compute_position_sizes to fall back to base_position_size.
    curves = daily_curves if daily_curves is not None else {}
    contrib_returns = (
        daily_strategy_contribution_returns
        if daily_strategy_contribution_returns is not None else {}
    )
    pool_returns = daily_pool_returns if daily_pool_returns is not None else []
    sector_fn = sector_provider
    if sector_fn is None:
        # Default to the YAML/yfinance-backed get_sector — `Callable[[str], str]`.
        from marketpulse.backtest.sector import get_sector as _get_sector
        sector_fn = _get_sector

    # Allocator call wrapped defensively: any kernel exception is captured
    # as an ENGINE_INVARIANT_ERROR(phase="allocation") audit row; the cycle
    # then skips place_order and proceeds to tick() so existing OPEN
    # positions can still close at horizon.
    allocation: AllocationResult | None
    allocator_error: Exception | None = None
    try:
        allocation = allocator(
            bids=bids,
            existing_positions=_position_snapshots(repository),
            cash_available=float(repository.cash_balance()),
            allocation_context=allocation_ctx,
            sizing_context=sizing_ctx,
            daily_curves=curves,
            daily_strategy_contribution_returns=contrib_returns,
            daily_pool_returns=pool_returns,
            sector_provider=sector_fn,
            price_provider=price_provider,
        )
    except Exception as e:
        allocator_error = e
        allocation = None
        with repository.transaction():
            repository.write_audit_event(
                event_type=AuditEventType.ENGINE_INVARIANT_ERROR,
                order_id=None,
                strategy=None,
                reason="allocator_failed",
                context={
                    "phase": "allocation",
                    "tick_date": tick_date.isoformat(),
                    "bid_count": len(bids),
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
                timestamp=clock.now(),
            )

    # === Phase 4: place_order per winner (skipped if allocator failed) ===
    placed = 0
    rejected = 0
    duplicates = 0
    if allocation is not None:
        for winner in allocation.winners:
            request = _make_order_request(
                winner=winner,
                allocation_run_id=allocation_run_id,
                allocation_date=tick_date,
                price_provider=price_provider,
            )
            try:
                result = engine.place_order(order_request=request)
                if result.created:
                    placed += 1
                elif result.duplicate:
                    duplicates += 1
            except OrderRejected:
                rejected += 1

    # === Phase 5: tick (always runs — close due positions even if
    # allocation failed, so existing OPEN positions still exit at horizon)
    tick_result = engine.tick(as_of=tick_date)

    # === Phase 6: TICK_COMPLETED ===
    # cycle_status is "completed_with_errors" if EITHER the allocator
    # threw OR the tick had per-row InvariantErrors.
    cycle_status: Literal["completed", "completed_with_errors"] = (
        "completed_with_errors"
        if (tick_result.errors or allocator_error is not None)
        else "completed"
    )
    result = DailyCycleResult(
        tick_date=tick_date,
        allocation_run_id=allocation_run_id,
        bids_collected=len(bids),
        orders_placed=placed,
        orders_rejected=rejected,
        duplicates_skipped=duplicates,
        entries_materialized=tick_result.entries_materialized,
        exits_materialized=tick_result.exits_materialized,
        tick_errors=tick_result.errors,
        cycle_status=cycle_status,
        cash_balance_end=repository.cash_balance(),
    )
    with repository.transaction():
        repository.write_tick_completed_once(
            tick_date=tick_date,
            context={
                "tick_date": tick_date.isoformat(),
                "status": cycle_status,
                "allocation_run_id": allocation_run_id,
                "bids_collected": result.bids_collected,
                "orders_placed": result.orders_placed,
                "orders_rejected": result.orders_rejected,
                "duplicates_skipped": result.duplicates_skipped,
                "entries_materialized": result.entries_materialized,
                "exits_materialized": result.exits_materialized,
                "tick_errors": [
                    {
                        "phase": e.phase,
                        "order_id": e.order_id,
                        "position_id": e.position_id,
                        "error": e.error,
                    }
                    for e in result.tick_errors
                ],
                "cash_balance_end": str(result.cash_balance_end),
            },
            timestamp=clock.now(),
        )
    return result
