"""Shared-pool simulator — Phase 5a.

Spec § 2: daily loop order strict CLOSE → BID → WEIGHT → DEDUP → ALLOC → MTM → RECORD.

This file is built in stages (Task 4 = scaffold with CLOSE+BID+WEIGHT;
Task 5 = DEDUP+ALLOC; Task 6 = MTM+RECORD+finalization). Intermediate
commits leave the function partially working but with tests passing
for the implemented steps.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from marketpulse.backtest.correlation import PriceProvider

from marketpulse.backtest.allocation import (
    AllocationContext,
    AllocationWinner,
    BidCandidate,
    BlockedBidReason,
    PositionSnapshot,
    SizingContext,
    allocate_for_day,
)
from marketpulse.backtest.contribution import (
    daily_contribution_return,
)
from marketpulse.backtest.metrics import compute_metrics
from marketpulse.backtest.trading_calendar import (
    build_calendar,
    elapsed_fraction,
)
from marketpulse.backtest.types import (
    BidRecord,
    PortfolioBacktestResult,
    StrategyContribution,
)
from marketpulse.logging import get_logger

log = get_logger(__name__)


@dataclass
class _OpenPosition:
    """Internal shared-pool position state."""
    strategy: str
    ticker: str
    entry_date: date
    entry_price: float
    horizon_date: date
    horizon_price: float
    position_size: float


def _decompose_day_contributions(
    *,
    today: date,
    realized_pnl_today_by_strategy: dict[str, float],
    mtm_prev_by_strategy: dict[str, float],
    mtm_today_by_strategy: dict[str, float],
    equity_curve: list[tuple[date, float]],
    initial_capital: float,
    daily_strategy_contribution_returns: dict[str, list[tuple[date, float]]],
    daily_pool_returns: list[tuple[date, float]],
) -> None:
    """Append per-strategy contribution returns + pool return for `today`.

    Spec § 5 + § 2 lock #7. Pure side-effect helper: mutates
    daily_strategy_contribution_returns and daily_pool_returns in place.
    Returns None to make the mutation explicit at the call site.

    Invariant: Σ daily_strategy_contribution_returns[s][-1] == daily_pool_returns[-1]
    by construction. Shared denominator (pool_equity_prev_day) means
    sum-of-divisions equals division-of-sum.

    Extracted from portfolio_simulator's daily loop in Phase 5e to keep
    the main loop legible (was inline ~50 LOC).
    """
    pool_equity_prev_day = (
        equity_curve[-1][1] if equity_curve else initial_capital
    )
    all_known_strategies = (
        set(realized_pnl_today_by_strategy)
        | set(mtm_prev_by_strategy)
        | set(mtm_today_by_strategy)
    )
    for s in all_known_strategies:
        pnl_today_s = (
            realized_pnl_today_by_strategy.get(s, 0.0)
            + mtm_today_by_strategy.get(s, 0.0)
            - mtm_prev_by_strategy.get(s, 0.0)
        )
        contrib_ret = daily_contribution_return(pnl_today_s, pool_equity_prev_day)
        daily_strategy_contribution_returns.setdefault(s, []).append((today, contrib_ret))
    pool_ret_today = sum(
        daily_strategy_contribution_returns[s][-1][1]
        for s in all_known_strategies
        if daily_strategy_contribution_returns.get(s)
    )
    daily_pool_returns.append((today, pool_ret_today))


def simulate_shared_pool(
    bids: list,
    daily_curves: dict[str, list[tuple[date, float]]],
    *,
    horizon: int,
    initial_capital: float = 10_000.0,
    base_position_size: float = 1_000.0,
    max_capital_in_use: float = 10_000.0,
    lookback_days: int = 60,
    target_vol: float = 0.01,
    min_position: float = 200.0,
    max_position: float = 4_000.0,
    sizing_enabled: bool = True,
    # NEW Phase 5c-1 sector cap (this task)
    sector_caps_enabled: bool = True,
    sector_cap_pct: float = 0.40,
    sector_provider: Callable[[str], str] | None = None,
    # NEW Phase 5c-2 correlation cap (reserved — wired in T7)
    correlation_caps_enabled: bool = True,
    correlation_cap_pct: float = 0.40,
    correlation_threshold: float = 0.60,
    price_provider: PriceProvider | None = None,
    # NEW Phase 5d contribution-adjusted Sharpe (reserved — wired in T6)
    contribution_enabled: bool = False,
    contribution_lambda: float = 0.5,
    # NEW Phase 5e (spec § 2 lock #6 + #12) — per-strategy sizing override.
    # Map strategy name to (base_override, min_override, max_override). Any
    # tuple element may be None to inherit the global default. Overrides
    # apply in BOTH sizing_enabled=True (vol-target × alpha-conviction) and
    # sizing_enabled=False (fixed) paths. Signal-layer purity preserved:
    # overrides never enter sigma/alpha/mean_alpha computations.
    per_strategy_overrides: dict[
        str, tuple[float | None, float | None, float | None]
    ] | None = None,
) -> PortfolioBacktestResult:
    """Phase 5a shared-pool simulator. See spec § 2 for algorithm.

    Provenance: every result carries bid_policy=f"rolling_sharpe_{lookback_days}d_v0"
    so dashboards and logs can distinguish runs that varied the lookback window.
    Default 60d matches spec § 8 decision #3; non-default lookbacks land in the
    result's bid_policy string so the source-of-truth window is never ambiguous.

    Phase 5b: SIZE COMPUTE step inserted between WEIGHT and DEDUP (spec § 2).
    Per-strategy size = base * (target_vol / σ_s) * (α_s / mean_α). When
    sizing_enabled=False, every strategy uses base_position_size (5a regression
    mode, sizing_policy='fixed_v0'). When True, sizing_policy='vol_target_conviction_v0'.

    NOTE (staged delivery): Task 6 wires per-strategy variable sizes through
    ALLOCATE (cap math + BidRecord position_size). Task 7 surfaces the
    finalization telemetry (n_size_too_small_skipped, avg_position_size on
    per-strategy contribution; max_strategy_exposure + hhi_concentration on
    the pool-level result).
    """
    # Phase 5a provenance — use f-string to thread lookback_days
    bid_policy = f"rolling_sharpe_{lookback_days}d_v0"
    # Phase 5b
    sizing_policy = "vol_target_conviction_v0" if sizing_enabled else "fixed_v0"

    # Phase 5d: bid_policy upgrade + composite provenance
    if contribution_enabled:
        bid_policy = f"contribution_adjusted_sharpe_{lookback_days}d_v0"
    contribution_policy = f"contribution_adjusted_sharpe_{lookback_days}d_v0"

    # Phase 5c risk_policy composition (spec § 10b)
    if sector_caps_enabled and correlation_caps_enabled:
        risk_policy = "cap40_corr06_enforced_v0"
    elif not sector_caps_enabled and not correlation_caps_enabled:
        risk_policy = "caps_disabled_v0"
    elif sector_caps_enabled:
        risk_policy = "cap40_only_v0"
    else:
        risk_policy = "corr06_only_v0"

    # Phase 5: sector_cap_dollars / correlation_cap_dollars were
    # computed as sector_cap_pct * initial_capital. The kernel
    # (allocate_for_day) now expresses the same caps as
    # sector_cap_pct * max_capital_in_use. The two coincide in the
    # default config (initial_capital == max_capital_in_use); the
    # frozen Phase 5 baseline confirms behavioral parity. If callers
    # ever pass divergent values, the cap math now uses
    # max_capital_in_use — re-validate the baseline.

    # Resolve sector_provider — default to real get_sector
    if sector_provider is None:
        from marketpulse.backtest.sector import get_sector as _real_get_sector
        sector_provider = _real_get_sector

    if not bids:
        from datetime import date as _date
        return PortfolioBacktestResult(
            horizon=horizon,
            n_trades=0,
            n_dedup_total=0,
            avg_capital_utilization=0.0,
            max_strategy_exposure=0.0,
            hhi_concentration=0.0,
            # Phase 5c placeholders — real values land in Tasks 6-8
            max_sector_exposure=0.0,
            max_sector_exposure_by_sector={},
            sector_breakdown={},
            max_neighbor_exposure=0.0,
            n_correlation_cap_events=0,
            cumulative_return=0.0,
            annual_return=0.0,
            sharpe=None,
            sortino=None,
            max_drawdown=0.0,
            calmar=None,
            win_rate=0.0,
            avg_win_pct=0.0,
            avg_loss_pct=0.0,
            daily_equity_curve=[(_date.today(), initial_capital)],
            excess_vs_spy=0.0,
            per_strategy_stats={},
            bid_history=[],
            bid_policy=bid_policy,
            sizing_policy=sizing_policy,
            sector_caps_enabled=sector_caps_enabled,
            correlation_caps_enabled=correlation_caps_enabled,
            risk_policy=risk_policy,
            contribution_enabled=contribution_enabled,
            contribution_policy=contribution_policy,
            contribution_lambda=contribution_lambda,
        )

    db_dates: set[date] = set()
    for b in bids:
        db_dates.add(b.event_time.date())
        db_dates.add(b.horizon_date)
    raw_dates = set(db_dates)
    min_d, max_d = min(raw_dates), max(raw_dates)
    cur = min_d
    while cur <= max_d:
        if cur.weekday() < 5:
            raw_dates.add(cur)
        cur += timedelta(days=1)
    calendar = build_calendar(list(raw_dates))

    bids_by_entry: dict[date, list] = {}
    for b in bids:
        bids_by_entry.setdefault(b.event_time.date(), []).append(b)

    cash: float = initial_capital
    open_positions: list[_OpenPosition] = []
    equity_curve: list[tuple[date, float]] = []
    all_bid_records: list[BidRecord] = []
    n_trades_by_strategy: dict[str, int] = {}
    trade_returns_by_strategy: dict[str, list[float]] = {}
    # Phase 5b: per-trade realized PnL (return × actual position size); the
    # uniform `realized = sum(r * base) for r in returns` shortcut breaks once
    # position sizes vary across trades within a strategy.
    trade_realized_pnl_by_strategy: dict[str, list[float]] = {}
    n_dedup_skipped_by_strategy: dict[str, int] = {}
    n_capacity_skipped_by_strategy: dict[str, int] = {}
    n_cash_short_skipped_by_strategy: dict[str, int] = {}
    n_floor_hits_by_strategy: dict[str, int] = {}
    n_size_too_small_by_strategy: dict[str, int] = {}
    # NEW Phase 5c counters
    n_sector_cap_skipped_by_strategy: dict[str, int] = {}
    n_correlation_cap_skipped_by_strategy: dict[str, int] = {}  # reserved for T7
    # Phase 5c sector + correlation telemetry accumulators
    sector_exposure_daily: list[dict[str, float]] = []
    n_correlation_cap_events = 0
    n_bids_by_strategy: dict[str, int] = {}
    bid_weights_by_strategy: dict[str, list[float]] = {}
    capital_in_use_by_day: list[float] = []
    exposure_by_strategy_by_day: dict[str, list[float]] = {}

    # Phase 5d per-day per-strategy accumulators (spec § 5).
    # realized_pnl_today_by_strategy is RESET every loop iteration; the other
    # three lists/dicts accumulate across the run for Phase 5d telemetry and
    # Task 6's pool_corr_excluding_self LOO subtraction.
    realized_pnl_today_by_strategy: dict[str, float] = {}
    daily_strategy_contribution_returns: dict[str, list[tuple[date, float]]] = {}
    daily_pool_returns: list[tuple[date, float]] = []
    pool_corr_by_strategy: dict[str, list[float | None]] = {}

    prev_d: date | None = None
    for d in calendar:
        # Phase 5d: snapshot per-strategy unrealized PnL using YESTERDAY's mark
        # BEFORE the CLOSE step. `open_positions` at this point is the set of
        # positions that were open at the END of the previous day. We use
        # prev_d (not d) as the "current" arg to elapsed_fraction so the mark
        # is yesterday's mark, not today's.
        mtm_prev_by_strategy: dict[str, float] = {}
        if prev_d is not None:
            for pos in open_positions:
                if pos.entry_date == prev_d:
                    # Opened yesterday → same-day no-MTM rule applied yesterday
                    unrealized_prev = 0.0
                else:
                    fraction_prev = elapsed_fraction(
                        calendar, entry=pos.entry_date,
                        horizon=pos.horizon_date, current=prev_d,
                    )
                    est_price_prev = pos.entry_price + (
                        pos.horizon_price - pos.entry_price
                    ) * fraction_prev
                    unrealized_prev = pos.position_size * (
                        est_price_prev / pos.entry_price - 1.0
                    )
                mtm_prev_by_strategy[pos.strategy] = (
                    mtm_prev_by_strategy.get(pos.strategy, 0.0) + unrealized_prev
                )

        # Phase 5d: reset per-day realized PnL bucket; populated in CLOSE.
        realized_pnl_today_by_strategy.clear()


        # ─── CLOSE ───
        still_open: list[_OpenPosition] = []
        for pos in open_positions:
            if pos.horizon_date == d:
                realized_ret = (pos.horizon_price - pos.entry_price) / pos.entry_price
                cash += pos.position_size * (1 + realized_ret)
                trade_returns_by_strategy.setdefault(pos.strategy, []).append(realized_ret)
                trade_realized_pnl_by_strategy.setdefault(pos.strategy, []).append(
                    realized_ret * pos.position_size
                )
                # Phase 5d: also accumulate into per-day bucket (cleared each loop)
                realized_pnl_today_by_strategy[pos.strategy] = (
                    realized_pnl_today_by_strategy.get(pos.strategy, 0.0)
                    + realized_ret * pos.position_size
                )
            else:
                still_open.append(pos)
        open_positions = still_open

        # ─── BID COLLECT ───
        in_flight_tickers = {p.ticker for p in open_positions}
        todays_raw_bids = [
            b for b in bids_by_entry.get(d, [])
            if b.ticker not in in_flight_tickers
        ]

        # ─── WEIGHT + SIZE + DEDUP + ALLOCATE ─── (delegated to kernel)
        # Build kernel inputs. PositionSnapshot encodes dollar-exposure
        # as quantity=1, entry_price=position_size — matches the kernel's
        # _dollar_exposure = quantity * entry_price contract.
        bid_candidates: list[BidCandidate] = [
            BidCandidate(
                strategy=b.strategy,
                ticker=b.ticker,
                event_time=b.event_time,
                event_price=b.event_price,
                horizon_date=b.horizon_date,
                horizon_price=b.horizon_price,
                strategy_version="",
            )
            for b in todays_raw_bids
        ]
        existing_snapshots: list[PositionSnapshot] = [
            PositionSnapshot(
                strategy=p.strategy,
                ticker=p.ticker,
                quantity=1,
                entry_price=p.position_size,
                sector=None,
                open_since=p.entry_date,
            )
            for p in open_positions
        ]
        alloc_ctx = AllocationContext(
            allocation_date=d,
            target_vol=target_vol,
            lookback_days=lookback_days,
            sector_caps_enabled=sector_caps_enabled,
            sector_cap_pct=sector_cap_pct,
            correlation_caps_enabled=correlation_caps_enabled,
            correlation_cap_pct=correlation_cap_pct,
            correlation_threshold=correlation_threshold,
            contribution_enabled=contribution_enabled,
            contribution_lambda=contribution_lambda,
            pool_corr_mode="LOO_ONLY_v0",
            phase5e_warm_pool_overlap_days=0,
            max_capital_in_use=max_capital_in_use,
        )
        sizing_ctx = SizingContext(
            base_position_size=base_position_size,
            min_position=min_position,
            max_position=max_position,
            sizing_enabled=sizing_enabled,
            per_strategy_overrides=per_strategy_overrides or {},
        )
        # Phase 5c sector cap is expressed against initial_capital,
        # while the kernel expresses it against max_capital_in_use.
        # The two coincide in Phase 5 default config (initial_capital
        # == max_capital_in_use == 10_000); see the public function
        # signature defaults. The cap-dollars math has been validated
        # against the frozen Phase 5 baseline; if the two ever diverge,
        # the cap-pct numerator must be patched here BEFORE building
        # the context.
        alloc_result = allocate_for_day(
            bids=bid_candidates,
            existing_positions=existing_snapshots,
            cash_available=cash,
            allocation_context=alloc_ctx,
            sizing_context=sizing_ctx,
            daily_curves=daily_curves,
            daily_strategy_contribution_returns=daily_strategy_contribution_returns,
            daily_pool_returns=daily_pool_returns,
            sector_provider=sector_provider,
            price_provider=price_provider,
        )

        # Floor-hit telemetry — extracted from the kernel.
        for s in alloc_result.floor_hits:
            n_floor_hits_by_strategy[s] = n_floor_hits_by_strategy.get(s, 0) + 1
        # Per-strategy pool_corr today (avg_pool_corr telemetry).
        for s, pc in alloc_result.pool_corr_today:
            pool_corr_by_strategy.setdefault(s, []).append(pc)

        # Helpers to translate kernel outputs back into the Phase 5
        # BidRecord shape that downstream finalization expects.
        def _phase5d_kwargs_from_event(event):
            return {
                "raw_bid_weight": event.raw_bid_weight,
                "pool_corr": event.pool_corr,
                "contribution_multiplier": event.contribution_multiplier,
                "adjusted_bid_weight": event.adjusted_bid_weight,
                "effective_corr_window": event.effective_corr_window,
                "rewarded_for_negative_corr": event.rewarded_for_negative_corr,
                "would_change_rank": event.would_change_rank,
            }

        for event in alloc_result.timeline:
            if isinstance(event, AllocationWinner):
                # WON outcome — also mutates open_positions + cash.
                all_bid_records.append(BidRecord(
                    date=d, strategy=event.strategy, ticker=event.ticker,
                    weight=event.weight,
                    outcome="won", winner=None,
                    position_size=event.position_size,
                    size_clamped_by_override=event.size_clamped_by_override,
                    **_phase5d_kwargs_from_event(event),
                ))
                n_trades_by_strategy[event.strategy] = (
                    n_trades_by_strategy.get(event.strategy, 0) + 1
                )
                n_bids_by_strategy[event.strategy] = (
                    n_bids_by_strategy.get(event.strategy, 0) + 1
                )
                bid_weights_by_strategy.setdefault(event.strategy, []).append(
                    event.weight
                )
                open_positions.append(_OpenPosition(
                    strategy=event.strategy, ticker=event.ticker,
                    entry_date=d, entry_price=event.event_price,
                    horizon_date=event.horizon_date,
                    horizon_price=event.horizon_price,
                    position_size=event.position_size,
                ))
            else:
                # BlockedBidReason — map reason → outcome literal.
                assert isinstance(event, BlockedBidReason)
                outcome = event.reason
                kwargs = {
                    "date": d,
                    "strategy": event.strategy,
                    "ticker": event.ticker,
                    "weight": event.weight,
                    "outcome": outcome,
                    "winner": event.winner,
                    "position_size": event.position_size,
                    "size_clamped_by_override": event.size_clamped_by_override,
                    **_phase5d_kwargs_from_event(event),
                }
                if outcome == "sector_cap_full" and event.blocked_by_sector:
                    kwargs["blocked_by_sector"] = event.blocked_by_sector
                if (
                    outcome == "correlation_cap_full"
                    and event.blocked_by_correlation_with
                ):
                    kwargs["blocked_by_correlation_with"] = (
                        event.blocked_by_correlation_with
                    )
                all_bid_records.append(BidRecord(**kwargs))

                # Counters
                if outcome == "size_too_small":
                    n_size_too_small_by_strategy[event.strategy] = (
                        n_size_too_small_by_strategy.get(event.strategy, 0) + 1
                    )
                    n_bids_by_strategy[event.strategy] = (
                        n_bids_by_strategy.get(event.strategy, 0) + 1
                    )
                    # NOTE: size_too_small intentionally does NOT update
                    # bid_weights_by_strategy — matches Phase 5 contract.
                elif outcome == "dedup_loser":
                    n_dedup_skipped_by_strategy[event.strategy] = (
                        n_dedup_skipped_by_strategy.get(event.strategy, 0) + 1
                    )
                    n_bids_by_strategy[event.strategy] = (
                        n_bids_by_strategy.get(event.strategy, 0) + 1
                    )
                    bid_weights_by_strategy.setdefault(
                        event.strategy, []
                    ).append(event.weight)
                else:
                    # cap_full / cash_short / sector_cap_full / correlation_cap_full
                    if outcome == "cap_full":
                        n_capacity_skipped_by_strategy[event.strategy] = (
                            n_capacity_skipped_by_strategy.get(
                                event.strategy, 0
                            ) + 1
                        )
                    elif outcome == "cash_short":
                        n_cash_short_skipped_by_strategy[event.strategy] = (
                            n_cash_short_skipped_by_strategy.get(
                                event.strategy, 0
                            ) + 1
                        )
                    elif outcome == "sector_cap_full":
                        n_sector_cap_skipped_by_strategy[event.strategy] = (
                            n_sector_cap_skipped_by_strategy.get(
                                event.strategy, 0
                            ) + 1
                        )
                    elif outcome == "correlation_cap_full":
                        n_correlation_cap_skipped_by_strategy[event.strategy] = (
                            n_correlation_cap_skipped_by_strategy.get(
                                event.strategy, 0
                            ) + 1
                        )
                        n_correlation_cap_events += 1
                    n_bids_by_strategy[event.strategy] = (
                        n_bids_by_strategy.get(event.strategy, 0) + 1
                    )
                    bid_weights_by_strategy.setdefault(
                        event.strategy, []
                    ).append(event.weight)

        # Cash is canonical from the kernel.
        cash = alloc_result.cash_remaining

        # Phase 5c: snapshot per-day sector exposure (post-ALLOCATE).
        # Recompute sector_by_ticker over the current open_positions —
        # the kernel's internal map isn't returned, but sector_provider
        # is idempotent (and the JSON cache makes it cheap).
        sector_by_ticker: dict[str, str] = {}
        for p in open_positions:
            sector_by_ticker.setdefault(p.ticker, sector_provider(p.ticker))
        day_snapshot: dict[str, float] = {}
        for p in open_positions:
            s = sector_by_ticker[p.ticker]
            day_snapshot[s] = day_snapshot.get(s, 0.0) + p.position_size
        sector_exposure_daily.append(day_snapshot)

        # ─── MTM ─── (linear interpolation per spec § 2 + Phase 4)
        positions_value = 0.0
        # Phase 5d: accumulate today's per-strategy unrealized PnL component
        # using the SAME per-position mark formula as the line above. Sum by
        # strategy → fed into the day-level contribution decomposition below.
        mtm_today_by_strategy: dict[str, float] = {}
        for pos in open_positions:
            if pos.entry_date == d:
                # Newly opened: no same-day MTM (matches Phase 4 invariant)
                positions_value += pos.position_size
                unrealized_today = 0.0
            else:
                fraction = elapsed_fraction(
                    calendar, entry=pos.entry_date,
                    horizon=pos.horizon_date, current=d,
                )
                est_price = pos.entry_price + (
                    pos.horizon_price - pos.entry_price
                ) * fraction
                positions_value += pos.position_size * (est_price / pos.entry_price)
                unrealized_today = pos.position_size * (
                    est_price / pos.entry_price - 1.0
                )
            mtm_today_by_strategy[pos.strategy] = (
                mtm_today_by_strategy.get(pos.strategy, 0.0) + unrealized_today
            )

        # ─── Phase 5d per-day per-strategy contribution decomposition ───
        # Helper extracted in Phase 5e (spec § 2 lock #7).
        _decompose_day_contributions(
            today=d,
            realized_pnl_today_by_strategy=realized_pnl_today_by_strategy,
            mtm_prev_by_strategy=mtm_prev_by_strategy,
            mtm_today_by_strategy=mtm_today_by_strategy,
            equity_curve=equity_curve,
            initial_capital=initial_capital,
            daily_strategy_contribution_returns=daily_strategy_contribution_returns,
            daily_pool_returns=daily_pool_returns,
        )

        # ─── RECORD ───
        equity_curve.append((d, cash + positions_value))
        capital_in_use_by_day.append(sum(p.position_size for p in open_positions))
        # Per-strategy exposure (snapshot of currently-deployed capital)
        all_strategies_seen = set(daily_curves.keys()) | set(n_bids_by_strategy.keys())
        for s in all_strategies_seen:
            exposure_by_strategy_by_day.setdefault(s, []).append(
                sum(p.position_size for p in open_positions if p.strategy == s)
                / initial_capital
            )

        # Phase 5d: advance previous-day pointer for tomorrow's mtm_prev snapshot.
        prev_d = d

    # ─── FINALIZE ───
    # Aggregate metrics over the COMBINED pool's daily curve
    all_returns: list[float] = []
    for s_returns in trade_returns_by_strategy.values():
        all_returns.extend(s_returns)
    n_trades = sum(n_trades_by_strategy.values())

    # Unrealized MTM of positions still open at end of window — attribute to
    # their strategy so Σ contribution_pnl == pool cumulative PnL.
    last_day = calendar[-1] if calendar else None
    unrealized_pnl_by_strategy: dict[str, float] = {}
    for pos in open_positions:
        if pos.entry_date == last_day:
            # Newly opened on last day — no MTM yet (matches RECORD step)
            unrealized = 0.0
        else:
            fraction = elapsed_fraction(
                calendar, entry=pos.entry_date,
                horizon=pos.horizon_date, current=last_day,
            )
            est_price = pos.entry_price + (
                pos.horizon_price - pos.entry_price
            ) * fraction
            unrealized = pos.position_size * (est_price / pos.entry_price - 1.0)
        unrealized_pnl_by_strategy[pos.strategy] = (
            unrealized_pnl_by_strategy.get(pos.strategy, 0.0) + unrealized
        )

    metrics = compute_metrics(
        equity_curve=equity_curve,
        n_trades=n_trades,
        trade_returns=all_returns,
    )

    # avg capital utilization across all days
    avg_util = (
        sum(c / max_capital_in_use for c in capital_in_use_by_day)
        / len(capital_in_use_by_day)
        if capital_in_use_by_day else 0.0
    )

    # Per-strategy contributions — iterate in sorted strategy-name order so
    # template row rendering is deterministic across runs (set() iteration is
    # arbitrary; insertion order leaks into PortfolioBacktestResult.per_strategy_stats
    # via dict semantics and would shuffle the strategy table).
    from marketpulse.strategies import load_strategies
    strategies_yaml = load_strategies()

    # Phase 5b Task 7: per-strategy won-bid position_size lists drive
    # avg_position_size telemetry. Built from BidRecords so the metric stays
    # source-of-truth aligned with bid_history.
    won_sizes_by_strategy: dict[str, list[float]] = {}
    for rec in all_bid_records:
        if rec.outcome == "won":
            won_sizes_by_strategy.setdefault(rec.strategy, []).append(
                rec.position_size
            )

    # Phase 5d: count would_change_rank per BID from all_bid_records
    n_would_change_rank_by_strategy: dict[str, int] = {}
    for b in all_bid_records:
        if b.would_change_rank:
            n_would_change_rank_by_strategy[b.strategy] = (
                n_would_change_rank_by_strategy.get(b.strategy, 0) + 1
            )

    # Phase 5d: avg_pool_corr per strategy (time-avg over non-None values)
    avg_pool_corr_by_strategy: dict[str, float | None] = {}
    for s, corr_list in pool_corr_by_strategy.items():
        defined = [c for c in corr_list if c is not None]
        avg_pool_corr_by_strategy[s] = (
            sum(defined) / len(defined) if defined else None
        )

    # Phase 5e Thread D — allocation observability (spec § 2 lock #14, #15, #16, #19)
    # Provenance: OBSERVABILITY_MODE == "v1" — spec § 2 lock #17.
    # Computed at finalization on EVERY run; downstream consumers (Phase 6
    # optimizer) read these fields unconditionally (lock #16).
    total_won_capital = sum(
        b.position_size for b in all_bid_records if b.outcome == "won"
    )
    effective_allocation_by_strategy: dict[str, float] = {}
    for s in sorted(daily_curves.keys()):
        won_size_s = sum(
            b.position_size for b in all_bid_records
            if b.strategy == s and b.outcome == "won"
        )
        effective_allocation_by_strategy[s] = (
            won_size_s / total_won_capital if total_won_capital > 0 else 0.0
        )

    # Compute rank_drift with locked tie-break (spec § 2 lock #19).
    # Both sorts use lexicographic ascending tie-break by strategy key.
    # Both iterate over the FULL key set (no zero-filtering). This makes
    # the two rankings permutations of the same set, so Σ drift == 0 is
    # a true permutation identity.
    all_strategy_keys = sorted(daily_curves.keys())
    # Compute per-strategy avg_bid_weight INLINE here (needed BEFORE the
    # per_strategy_stats loop builds it). Uses the same formula the loop
    # uses (mean of b.weight over all bids for the strategy).
    avg_bid_weight_by_strategy: dict[str, float] = {}
    for s in all_strategy_keys:
        bids_for_s = [b for b in all_bid_records if b.strategy == s]
        if bids_for_s:
            avg_bid_weight_by_strategy[s] = (
                sum(b.weight for b in bids_for_s) / len(bids_for_s)
            )
        else:
            avg_bid_weight_by_strategy[s] = 0.0

    sorted_by_weight = sorted(
        all_strategy_keys,
        key=lambda s: (-avg_bid_weight_by_strategy[s], s),
    )
    sorted_by_capital = sorted(
        all_strategy_keys,
        key=lambda s: (-effective_allocation_by_strategy[s], s),
    )
    rank_by_weight = {s: i for i, s in enumerate(sorted_by_weight)}
    rank_by_capital = {s: i for i, s in enumerate(sorted_by_capital)}
    rank_drift_by_strategy: dict[str, int] = {
        s: rank_by_weight[s] - rank_by_capital[s]
        for s in all_strategy_keys
    }

    per_strategy_stats: dict[str, StrategyContribution] = {}
    for s in sorted(daily_curves.keys()):
        # Phase 5b: realized PnL uses per-trade actual position size (variable),
        # not the uniform `base * return` shortcut (Phase 5a invariant).
        realized = sum(trade_realized_pnl_by_strategy.get(s, []))
        unrealized = unrealized_pnl_by_strategy.get(s, 0.0)
        contrib_pnl = realized + unrealized
        exposures = exposure_by_strategy_by_day.get(s, [])
        avg_exposure = sum(exposures) / len(exposures) if exposures else 0.0
        bid_w_list = bid_weights_by_strategy.get(s, [])
        avg_bid_weight = sum(bid_w_list) / len(bid_w_list) if bid_w_list else 0.0
        won_sizes = won_sizes_by_strategy.get(s, [])
        avg_position_size = sum(won_sizes) / len(won_sizes) if won_sizes else 0.0
        per_strategy_stats[s] = StrategyContribution(
            strategy=s,
            display_name=(
                strategies_yaml[s].display_name if s in strategies_yaml else s
            ),
            n_trades=n_trades_by_strategy.get(s, 0),
            n_dedup_skipped=n_dedup_skipped_by_strategy.get(s, 0),
            n_capacity_skipped=n_capacity_skipped_by_strategy.get(s, 0),
            n_cash_short_skipped=n_cash_short_skipped_by_strategy.get(s, 0),
            n_size_too_small_skipped=n_size_too_small_by_strategy.get(s, 0),
            # Phase 5c: counters wired in T6/T7; full telemetry lands in T8
            n_sector_cap_skipped=n_sector_cap_skipped_by_strategy.get(s, 0),
            n_correlation_cap_skipped=n_correlation_cap_skipped_by_strategy.get(s, 0),
            contribution_pnl=contrib_pnl,
            avg_exposure=avg_exposure,
            avg_bid_weight=avg_bid_weight,
            avg_position_size=avg_position_size,
            n_bids=n_bids_by_strategy.get(s, 0),
            n_floor_hits=n_floor_hits_by_strategy.get(s, 0),
            avg_pool_corr=avg_pool_corr_by_strategy.get(s),
            n_would_change_rank=n_would_change_rank_by_strategy.get(s, 0),
            # NEW Phase 5e
            effective_allocation=effective_allocation_by_strategy.get(s, 0.0),
            rank_drift_from_signal=rank_drift_by_strategy.get(s, 0),
        )

    # Phase 5b Task 7: portfolio-level concentration telemetry.
    # max_strategy_exposure = peak single-strategy avg_exposure across pool.
    # hhi_concentration = Σ(exposure_s²) — Herfindahl-Hirschman Index.
    if per_strategy_stats:
        _exposures = [c.avg_exposure for c in per_strategy_stats.values()]
        max_strategy_exposure = max(_exposures) if _exposures else 0.0
        hhi_concentration = sum(e * e for e in _exposures)
    else:
        max_strategy_exposure = 0.0
        hhi_concentration = 0.0

    # Phase 5c-1 sector telemetry
    if sector_exposure_daily:
        max_sector_exposure = 0.0
        max_sector_exposure_by_sector: dict[str, float] = {}
        sector_sum_over_days: dict[str, float] = {}

        for day_snapshot in sector_exposure_daily:
            for s, dollars in day_snapshot.items():
                frac = dollars / initial_capital
                if frac > max_sector_exposure:
                    max_sector_exposure = frac
                if frac > max_sector_exposure_by_sector.get(s, 0.0):
                    max_sector_exposure_by_sector[s] = frac
                sector_sum_over_days[s] = sector_sum_over_days.get(s, 0.0) + frac

        n_days = len(sector_exposure_daily)
        sector_breakdown = {s: total / n_days for s, total in sector_sum_over_days.items()}
    else:
        max_sector_exposure = 0.0
        max_sector_exposure_by_sector = {}
        sector_breakdown = {}

    # Phase 5c-2 correlation telemetry.
    # max_neighbor_exposure is a SPEC-LOCKED v0 PLACEHOLDER (spec § 7 + plan
    # T8 explicitly state "stays 0.0 in v0; computation deferred to future
    # iteration"). The field is reserved on PortfolioBacktestResult so the
    # schema is stable for future versions; the lab UI does not surface this
    # number anywhere in v0, so the 0.0 is never rendered to users.
    # See `n_correlation_cap_events` for the v0 correlation-cap signal that
    # IS computed.
    max_neighbor_exposure = 0.0

    # Last-100 slice of bid history (spec § 4: render-layer cap)
    bid_history = all_bid_records[-100:] if len(all_bid_records) > 100 else all_bid_records

    return PortfolioBacktestResult(
        horizon=horizon,
        n_trades=n_trades,
        n_dedup_total=sum(n_dedup_skipped_by_strategy.values()),
        avg_capital_utilization=avg_util,
        max_strategy_exposure=max_strategy_exposure,
        hhi_concentration=hhi_concentration,
        # Phase 5c-1 sector telemetry (Task 8)
        max_sector_exposure=max_sector_exposure,
        max_sector_exposure_by_sector=max_sector_exposure_by_sector,
        sector_breakdown=sector_breakdown,
        # Phase 5c-2 correlation telemetry (Task 8)
        max_neighbor_exposure=max_neighbor_exposure,
        n_correlation_cap_events=n_correlation_cap_events,
        cumulative_return=metrics.cumulative_return,
        annual_return=metrics.annual_return,
        sharpe=metrics.sharpe,
        sortino=metrics.sortino,
        max_drawdown=metrics.max_drawdown,
        calmar=metrics.calmar,
        win_rate=metrics.win_rate,
        avg_win_pct=metrics.avg_win_pct,
        avg_loss_pct=metrics.avg_loss_pct,
        daily_equity_curve=equity_curve,
        excess_vs_spy=0.0,  # orchestrator (Task 7) overrides with combined - SPY
        per_strategy_stats=per_strategy_stats,
        bid_history=bid_history,
        bid_policy=bid_policy,
        sizing_policy=sizing_policy,
        sector_caps_enabled=sector_caps_enabled,
        correlation_caps_enabled=correlation_caps_enabled,
        risk_policy=risk_policy,
        contribution_enabled=contribution_enabled,
        contribution_policy=contribution_policy,
        contribution_lambda=contribution_lambda,
    )
