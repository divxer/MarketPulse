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

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from marketpulse.backtest.contribution import (
    BidWeightMetadata,
    compute_adjusted_bid_weight,
    pool_corr_excluding_self,
)
from marketpulse.backtest.correlation import find_correlation_neighbors
from marketpulse.backtest.policy import MIN_OVERLAP_DAYS
from marketpulse.backtest.sharpe import (
    compute_bid_weights,
    compute_position_sizes,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from marketpulse.backtest.correlation import PriceProvider

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
    OrderRequest construction.

    Phase 5 (dollar-sized backtest) consumes ``position_size`` and ignores
    ``quantity``; Phase 6 (share-based forward orders) populates
    ``quantity`` and may compute it from ``position_size``."""
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
    position_size: float = 0.0


@dataclass(frozen=True)
class BlockedBidReason:
    """Reason a bid did not become a winner. Typed dataclass replaces
    the earlier 'tuple[object, ...]' to prevent architecture erosion.

    Extended for 6a-0.4 with the diagnostic fields portfolio_simulator
    needs to reconstruct the exact Phase 5 BidRecord shape."""
    strategy: str
    ticker: str
    reason: str          # one of: dedup_loser | cap_full | sector_cap_full
                         # | correlation_cap_full | cash_short | size_too_small
    weight: float | None
    raw_bid_weight: float | None
    pool_corr: float | None
    contribution_multiplier: float
    adjusted_bid_weight: float | None
    # Phase 5 BidRecord reconstruction support (6a-0.4)
    position_size: float = 0.0
    winner: str | None = None
    blocked_by_sector: str | None = None
    blocked_by_correlation_with: tuple[tuple[str, float], ...] = ()
    effective_corr_window: int = 0
    rewarded_for_negative_corr: bool = False
    would_change_rank: bool = False
    size_clamped_by_override: bool = False


@dataclass(frozen=True)
class AllocationResult:
    """Outcome of one per-day allocation call.

    Authority contract: the allocator is the CANONICAL source for
    cash_used / cash_remaining for this batch. Callers (daily_cycle,
    simulate_shared_pool) MUST NOT recompute these values from the
    winners list — that introduces derived-state duplication and silent
    drift. Read them as-is.

    ``timeline`` is the canonical bid-by-bid event order — size_too_small
    blocks first (in source order), then dedup_losers (in DEDUP encounter
    order), then ALLOCATE iteration order with winners and cap/cash/
    sector/correlation blocks interleaved exactly as the kernel decided
    them. Callers that build a flat bid_history list consume timeline so
    the Phase 5 ordering is preserved byte-for-byte."""
    winners: tuple[AllocationWinner, ...]
    blocked: tuple[BlockedBidReason, ...]
    cash_used: float        # canonical — do not recompute
    cash_remaining: float   # canonical — do not recompute
    timeline: tuple[AllocationWinner | BlockedBidReason, ...] = ()
    # Phase 5 WEIGHT-step telemetry — strategies whose raw Sharpe hit
    # the negative-floor clamp this day. Phase 6 callers may ignore.
    floor_hits: tuple[str, ...] = ()
    # The full set of strategies that produced at least one bid today,
    # in sorted order (i.e. ``strategies_today`` post size-filter
    # removal would re-derive a different set; this is the pre-filter
    # set, exactly what compute_bid_weights ran on).
    strategies_today: tuple[str, ...] = ()
    # Phase 5 WEIGHT-step telemetry — per-strategy pool_corr computed
    # this day (LOO mode). None when overlap < min_overlap or variance
    # is zero. Mirrors the inline state Phase 5 used to thread into
    # avg_pool_corr_by_strategy. Frozen tuple of (strategy, pool_corr).
    pool_corr_today: tuple[tuple[str, float | None], ...] = ()


def allocate_for_day(
    *,
    bids: list[BidCandidate],
    existing_positions: list[PositionSnapshot],
    cash_available: float,
    allocation_context: AllocationContext,
    sizing_context: SizingContext,
    daily_curves: dict[str, list[tuple[date, float]]],
    daily_strategy_contribution_returns: dict[str, list[tuple[date, float]]],
    daily_pool_returns: list[tuple[date, float]],
    sector_provider: Callable[[str], str],
    price_provider: PriceProvider | None = None,
) -> AllocationResult:
    """Pure-function per-day allocation kernel (6a-0.4).

    Lifts WEIGHT → SIZE → DEDUP → ALLOCATE out of simulate_shared_pool.
    Inputs are explicit. Side effects are NONE — the function reads
    daily_curves / contribution returns / pool returns but does not
    mutate them.

    The kernel produces:
      - winners: bids that survived all gates, in deterministic order
      - blocked: bids rejected by any gate, in encounter order
      - cash_used / cash_remaining: canonical batch totals
    """
    d = allocation_context.allocation_date
    sector_cap_dollars = (
        allocation_context.sector_cap_pct
        * allocation_context.max_capital_in_use
    )
    correlation_cap_dollars = (
        allocation_context.correlation_cap_pct
        * allocation_context.max_capital_in_use
    )

    winners_out: list[AllocationWinner] = []
    blocked_out: list[BlockedBidReason] = []
    timeline: list[AllocationWinner | BlockedBidReason] = []
    cash = cash_available
    cash_used = 0.0

    # ─── WEIGHT COMPUTE ───
    strategies_today = sorted({b.strategy for b in bids})
    strategies_today_orig = tuple(strategies_today)
    weights_raw: dict[str, float | None] = {}
    weights: dict[str, float | None] = {}
    bid_weight_metadata: dict[str, BidWeightMetadata] = {}
    floor_hits_set: set[str] = set()

    if strategies_today:
        weights_raw, floor_hits_set = compute_bid_weights(
            strategies_today, daily_curves,
            as_of=d, lookback_days=allocation_context.lookback_days,
        )

        weights_adjusted: dict[str, float | None] = {}
        for s in strategies_today:
            raw = weights_raw.get(s)
            pool_corr, eff_window = pool_corr_excluding_self(
                daily_strategy_contribution_returns.get(s, []),
                daily_pool_returns,
                as_of=d,
                lookback_days=allocation_context.lookback_days,
                min_overlap=MIN_OVERLAP_DAYS,
            )
            adjusted, multiplier, rewarded = compute_adjusted_bid_weight(
                raw_sharpe=raw,
                pool_corr=pool_corr,
                lam=allocation_context.contribution_lambda,
                clip_min=0.5,
                clip_max=1.2,
            )
            weights_adjusted[s] = adjusted
            bid_weight_metadata[s] = BidWeightMetadata(
                raw=raw, pool_corr=pool_corr,
                multiplier=multiplier, adjusted=adjusted,
                effective_window=eff_window,
                rewarded_for_negative_corr=rewarded,
                would_change_rank=False,
            )

        # Compute would_change_rank for EVERY strategy
        sorted_raw = sorted(
            strategies_today,
            key=lambda s: (-(weights_raw.get(s) or 0.0), s),
        )
        sorted_adj = sorted(
            strategies_today,
            key=lambda s: (-(weights_adjusted.get(s) or 0.0), s),
        )
        rank_raw = {s: i for i, s in enumerate(sorted_raw)}
        rank_adj = {s: i for i, s in enumerate(sorted_adj)}
        for s in strategies_today:
            if rank_raw[s] != rank_adj[s]:
                bid_weight_metadata[s] = dataclasses.replace(
                    bid_weight_metadata[s], would_change_rank=True,
                )

        weights = (
            weights_adjusted if allocation_context.contribution_enabled
            else weights_raw
        )

    def _block(reason: BlockedBidReason) -> None:
        blocked_out.append(reason)
        timeline.append(reason)

    def _meta_kwargs(strategy: str) -> dict:
        meta = bid_weight_metadata.get(strategy)
        if meta is None:
            return {
                "raw_bid_weight": None,
                "pool_corr": None,
                "contribution_multiplier": 1.0,
                "adjusted_bid_weight": None,
                "effective_corr_window": 0,
                "rewarded_for_negative_corr": False,
                "would_change_rank": False,
            }
        return {
            "raw_bid_weight": meta.raw,
            "pool_corr": meta.pool_corr,
            "contribution_multiplier": meta.multiplier,
            "adjusted_bid_weight": meta.adjusted,
            "effective_corr_window": meta.effective_window,
            "rewarded_for_negative_corr": meta.rewarded_for_negative_corr,
            "would_change_rank": meta.would_change_rank,
        }

    # ─── SIZE COMPUTE ───
    overrides_map = sizing_context.per_strategy_overrides or {}
    position_sizes: dict[str, float | None] = {}
    clamped_by_override: dict[str, bool] = {}
    raw_sizes_below_min: dict[str, float] = {}

    if sizing_context.sizing_enabled and strategies_today:
        position_sizes, raw_sizes_below_min, clamped_by_override = (
            compute_position_sizes(
                strategies_today, daily_curves,
                as_of=d,
                base=sizing_context.base_position_size,
                target_vol=allocation_context.target_vol,
                min_position=sizing_context.min_position,
                max_position=sizing_context.max_position,
                lookback_days=allocation_context.lookback_days,
                per_strategy_overrides=overrides_map,
            )
        )
    else:
        for s in strategies_today:
            ov_base, ov_min, ov_max = overrides_map.get(s, (None, None, None))
            eff_base = (
                ov_base if ov_base is not None
                else sizing_context.base_position_size
            )
            eff_min = (
                ov_min if ov_min is not None else sizing_context.min_position
            )
            eff_max = (
                ov_max if ov_max is not None else sizing_context.max_position
            )
            raw = eff_base
            clamped_by_override[s] = raw > eff_max
            if raw < eff_min:
                position_sizes[s] = None
                raw_sizes_below_min[s] = raw
            else:
                position_sizes[s] = min(raw, eff_max)

    # ─── SIZE FILTER ─── (size_too_small outcome)
    todays_bids = list(bids)
    if strategies_today:
        strategies_skipped_by_size = {
            s for s, sz in position_sizes.items() if sz is None
        }
        if strategies_skipped_by_size:
            kept: list[BidCandidate] = []
            for b in todays_bids:
                if b.strategy in strategies_skipped_by_size:
                    _block(BlockedBidReason(
                        strategy=b.strategy,
                        ticker=b.ticker,
                        reason="size_too_small",
                        weight=weights[b.strategy],
                        position_size=raw_sizes_below_min[b.strategy],
                        size_clamped_by_override=False,
                        **_meta_kwargs(b.strategy),
                    ))
                else:
                    kept.append(b)
            todays_bids = kept
            strategies_today = [
                s for s in strategies_today
                if s not in strategies_skipped_by_size
            ]

    # ─── DEDUP (same-day same-ticker collision) ───
    bids_by_ticker: dict[str, list[BidCandidate]] = {}
    for b in todays_bids:
        bids_by_ticker.setdefault(b.ticker, []).append(b)
    dedup_winners: dict[str, BidCandidate] = {}
    for ticker, group in bids_by_ticker.items():
        best = min(group, key=lambda b: (
            -weights[b.strategy], b.event_time, b.strategy,
        ))
        dedup_winners[ticker] = best
        for loser in group:
            if loser is not best:
                _block(BlockedBidReason(
                    strategy=loser.strategy,
                    ticker=ticker,
                    reason="dedup_loser",
                    weight=weights[loser.strategy],
                    position_size=position_sizes[loser.strategy] or 0.0,
                    winner=best.strategy,
                    size_clamped_by_override=clamped_by_override.get(
                        loser.strategy, False,
                    ),
                    **_meta_kwargs(loser.strategy),
                ))

    # ─── ALLOCATE ───
    sorted_winners = sorted(
        dedup_winners.values(),
        key=lambda b: (-weights[b.strategy], b.event_time, b.strategy),
    )

    # Pre-warm sector lookup
    sector_by_ticker: dict[str, str] = {}
    for p in existing_positions:
        sector_by_ticker.setdefault(p.ticker, sector_provider(p.ticker))
    for b in sorted_winners:
        sector_by_ticker.setdefault(b.ticker, sector_provider(b.ticker))

    # Sector exposure starts at the snapshot's existing positions.
    # Dollar exposure per position is quantity * entry_price (Phase 6
    # share-based) or, for Phase 5 dollar-based callers that pass quantity=1
    # and entry_price=dollars, simply entry_price.
    def _dollar_exposure(p: PositionSnapshot) -> float:
        return float(p.quantity) * p.entry_price

    sector_exposure: dict[str, float] = {}
    for p in existing_positions:
        s = sector_by_ticker[p.ticker]
        sector_exposure[s] = sector_exposure.get(s, 0.0) + _dollar_exposure(p)

    # Running open-positions list (for cap math + correlation neighbors).
    # We track (ticker, dollar_exposure) for each in-flight position, both
    # the snapshot's pre-existing ones AND the ones we add this call.
    running_open: list[tuple[str, float]] = [
        (p.ticker, _dollar_exposure(p)) for p in existing_positions
    ]

    for b in sorted_winners:
        requested_size = position_sizes[b.strategy]
        # size_too_small filtered upstream; defensive guard for `python -O`
        # (asserts are stripped under -O so we use a real branch).
        if requested_size is None:
            continue
        capital_in_use = sum(sz for _t, sz in running_open)

        if capital_in_use + requested_size > allocation_context.max_capital_in_use:
            _block(BlockedBidReason(
                strategy=b.strategy,
                ticker=b.ticker,
                reason="cap_full",
                weight=weights[b.strategy],
                position_size=requested_size,
                size_clamped_by_override=clamped_by_override.get(
                    b.strategy, False,
                ),
                **_meta_kwargs(b.strategy),
            ))
            continue
        if cash < requested_size:
            _block(BlockedBidReason(
                strategy=b.strategy,
                ticker=b.ticker,
                reason="cash_short",
                weight=weights[b.strategy],
                position_size=requested_size,
                size_clamped_by_override=clamped_by_override.get(
                    b.strategy, False,
                ),
                **_meta_kwargs(b.strategy),
            ))
            continue
        candidate_sector = sector_by_ticker[b.ticker]
        if (
            allocation_context.sector_caps_enabled
            and sector_exposure.get(candidate_sector, 0.0) + requested_size
            > sector_cap_dollars
        ):
            _block(BlockedBidReason(
                strategy=b.strategy,
                ticker=b.ticker,
                reason="sector_cap_full",
                weight=weights[b.strategy],
                position_size=requested_size,
                blocked_by_sector=candidate_sector,
                size_clamped_by_override=clamped_by_override.get(
                    b.strategy, False,
                ),
                **_meta_kwargs(b.strategy),
            ))
            continue
        if (
            allocation_context.correlation_caps_enabled
            and price_provider is not None
        ):
            open_tickers = [t for t, _sz in running_open]
            neighbors, corr_diagnostics = find_correlation_neighbors(
                b.ticker, open_tickers,
                as_of=d, threshold=allocation_context.correlation_threshold,
                lookback_days=allocation_context.lookback_days,
                price_provider=price_provider,
            )
            cluster_exposure = requested_size + sum(
                sz for t, sz in running_open if t in neighbors
            )
            if cluster_exposure > correlation_cap_dollars:
                _block(BlockedBidReason(
                    strategy=b.strategy,
                    ticker=b.ticker,
                    reason="correlation_cap_full",
                    weight=weights[b.strategy],
                    position_size=requested_size,
                    blocked_by_correlation_with=corr_diagnostics,
                    size_clamped_by_override=clamped_by_override.get(
                        b.strategy, False,
                    ),
                    **_meta_kwargs(b.strategy),
                ))
                continue

        # Survived all gates — record winner + update running state.
        meta = bid_weight_metadata.get(b.strategy)
        winner = AllocationWinner(
            strategy=b.strategy,
            ticker=b.ticker,
            event_time=b.event_time,
            event_price=b.event_price,
            horizon_date=b.horizon_date,
            horizon_price=b.horizon_price,
            quantity=0,  # Phase 5 uses dollar-sizing; quantity reserved for Phase 6
            weight=weights[b.strategy] if weights[b.strategy] is not None else 0.0,
            raw_bid_weight=meta.raw if meta else None,
            pool_corr=meta.pool_corr if meta else None,
            contribution_multiplier=meta.multiplier if meta else 1.0,
            adjusted_bid_weight=meta.adjusted if meta else None,
            effective_corr_window=meta.effective_window if meta else 0,
            rewarded_for_negative_corr=(
                meta.rewarded_for_negative_corr if meta else False
            ),
            would_change_rank=meta.would_change_rank if meta else False,
            size_clamped_by_override=clamped_by_override.get(
                b.strategy, False,
            ),
            strategy_version=b.strategy_version,
            position_size=requested_size,
        )
        winners_out.append(winner)
        timeline.append(winner)
        running_open.append((b.ticker, requested_size))
        sector_exposure[candidate_sector] = (
            sector_exposure.get(candidate_sector, 0.0) + requested_size
        )
        cash -= requested_size
        cash_used += requested_size

    pool_corr_today_tuple = tuple(
        (s, bid_weight_metadata[s].pool_corr)
        for s in strategies_today_orig
        if s in bid_weight_metadata
    )

    return AllocationResult(
        winners=tuple(winners_out),
        blocked=tuple(blocked_out),
        cash_used=cash_used,
        cash_remaining=cash,
        timeline=tuple(timeline),
        floor_hits=tuple(sorted(floor_hits_set)),
        strategies_today=strategies_today_orig,
        pool_corr_today=pool_corr_today_tuple,
    )
