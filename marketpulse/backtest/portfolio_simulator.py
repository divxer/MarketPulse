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

from marketpulse.backtest.metrics import compute_metrics  # noqa: F401  (used in T6)
from marketpulse.backtest.sharpe import compute_bid_weights
from marketpulse.backtest.trading_calendar import (
    build_calendar,
    elapsed_fraction,  # noqa: F401  (used in T6 MTM)
)
from marketpulse.backtest.types import (
    BidRecord,
    PortfolioBacktestResult,
    StrategyContribution,  # noqa: F401  (used in T6 finalization)
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


def simulate_shared_pool(
    bids: list,
    daily_curves: dict[str, list[tuple[date, float]]],
    *,
    horizon: int,
    initial_capital: float = 10_000.0,
    position_size: float = 1_000.0,
    max_capital_in_use: float = 10_000.0,
    lookback_days: int = 60,
) -> PortfolioBacktestResult:
    """Phase 5a shared-pool simulator. See spec § 2 for algorithm."""
    if not bids:
        from datetime import date as _date
        return PortfolioBacktestResult(
            horizon=horizon,
            n_trades=0,
            n_dedup_total=0,
            avg_capital_utilization=0.0,
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
    n_dedup_skipped_by_strategy: dict[str, int] = {}
    n_capacity_skipped_by_strategy: dict[str, int] = {}
    n_cash_short_skipped_by_strategy: dict[str, int] = {}
    n_floor_hits_by_strategy: dict[str, int] = {}
    n_bids_by_strategy: dict[str, int] = {}
    bid_weights_by_strategy: dict[str, list[float]] = {}
    capital_in_use_by_day: list[float] = []
    exposure_by_strategy_by_day: dict[str, list[float]] = {}

    for d in calendar:
        # ─── CLOSE ───
        still_open: list[_OpenPosition] = []
        for pos in open_positions:
            if pos.horizon_date == d:
                realized_ret = (pos.horizon_price - pos.entry_price) / pos.entry_price
                cash += pos.position_size * (1 + realized_ret)
                trade_returns_by_strategy.setdefault(pos.strategy, []).append(realized_ret)
            else:
                still_open.append(pos)
        open_positions = still_open

        # ─── BID COLLECT ───
        in_flight_tickers = {p.ticker for p in open_positions}
        todays_bids = [
            b for b in bids_by_entry.get(d, [])
            if b.ticker not in in_flight_tickers
        ]

        # ─── WEIGHT COMPUTE ───
        strategies_today = sorted({b.strategy for b in todays_bids})
        weights: dict[str, float] = {}
        if strategies_today:
            weights = compute_bid_weights(
                strategies_today, daily_curves,
                as_of=d, lookback_days=lookback_days,
            )

        # Track n_floor_hits (weights at 0.1 came from a negative-Sharpe floor)
        for s in strategies_today:
            if weights.get(s) == 0.1:
                from marketpulse.backtest.sharpe import rolling_sharpe
                raw = rolling_sharpe(
                    daily_curves[s], as_of=d, lookback_days=lookback_days,
                )
                if raw is not None and raw < 0.1:
                    n_floor_hits_by_strategy[s] = n_floor_hits_by_strategy.get(s, 0) + 1

        # ─── DEDUP (same-day same-ticker collision) ───
        bids_by_ticker: dict[str, list] = {}
        for b in todays_bids:
            bids_by_ticker.setdefault(b.ticker, []).append(b)
        winners: dict[str, object] = {}
        for ticker, group in bids_by_ticker.items():
            # 3-key composite: (-weight, event_time, strategy_name)
            best = min(group, key=lambda b: (
                -weights[b.strategy], b.event_time, b.strategy,
            ))
            winners[ticker] = best
            for loser in group:
                if loser is not best:
                    all_bid_records.append(BidRecord(
                        date=d, strategy=loser.strategy, ticker=ticker,
                        weight=weights[loser.strategy],
                        outcome="dedup_loser", winner=best.strategy,
                    ))
                    n_dedup_skipped_by_strategy[loser.strategy] = (
                        n_dedup_skipped_by_strategy.get(loser.strategy, 0) + 1
                    )
                    # Loser still counts as a bid (for n_bids + avg_bid_weight)
                    n_bids_by_strategy[loser.strategy] = (
                        n_bids_by_strategy.get(loser.strategy, 0) + 1
                    )
                    bid_weights_by_strategy.setdefault(loser.strategy, []).append(
                        weights[loser.strategy]
                    )

        # ─── ALLOCATE (capital-constrained, greedy by weight desc) ───
        sorted_winners = sorted(
            winners.values(),
            key=lambda b: (-weights[b.strategy], b.event_time, b.strategy),
        )
        for b in sorted_winners:
            n_bids_by_strategy[b.strategy] = n_bids_by_strategy.get(b.strategy, 0) + 1
            bid_weights_by_strategy.setdefault(b.strategy, []).append(
                weights[b.strategy]
            )
            capital_in_use = sum(p.position_size for p in open_positions)
            if capital_in_use + position_size > max_capital_in_use:
                all_bid_records.append(BidRecord(
                    date=d, strategy=b.strategy, ticker=b.ticker,
                    weight=weights[b.strategy],
                    outcome="cap_full", winner=None,
                ))
                n_capacity_skipped_by_strategy[b.strategy] = (
                    n_capacity_skipped_by_strategy.get(b.strategy, 0) + 1
                )
                continue
            if cash < position_size:
                all_bid_records.append(BidRecord(
                    date=d, strategy=b.strategy, ticker=b.ticker,
                    weight=weights[b.strategy],
                    outcome="cash_short", winner=None,
                ))
                n_cash_short_skipped_by_strategy[b.strategy] = (
                    n_cash_short_skipped_by_strategy.get(b.strategy, 0) + 1
                )
                continue
            open_positions.append(_OpenPosition(
                strategy=b.strategy, ticker=b.ticker,
                entry_date=d, entry_price=b.event_price,
                horizon_date=b.horizon_date, horizon_price=b.horizon_price,
                position_size=position_size,
            ))
            cash -= position_size
            n_trades_by_strategy[b.strategy] = n_trades_by_strategy.get(b.strategy, 0) + 1
            all_bid_records.append(BidRecord(
                date=d, strategy=b.strategy, ticker=b.ticker,
                weight=weights[b.strategy],
                outcome="won", winner=None,
            ))

        # ─── MTM, RECORD — Task 6 fills proper MTM. Stub: cash + raw positions. ───
        equity_curve.append((d, cash + sum(p.position_size for p in open_positions)))
        capital_in_use_by_day.append(sum(p.position_size for p in open_positions))
        for s in strategies_today:
            exposure_by_strategy_by_day.setdefault(s, []).append(
                sum(p.position_size for p in open_positions if p.strategy == s)
                / initial_capital
            )

    n_trades = sum(n_trades_by_strategy.values())
    return PortfolioBacktestResult(
        horizon=horizon,
        n_trades=n_trades,
        n_dedup_total=sum(n_dedup_skipped_by_strategy.values()),
        avg_capital_utilization=(
            sum(capital_in_use_by_day) / (max_capital_in_use * len(capital_in_use_by_day))
            if capital_in_use_by_day else 0.0
        ),
        cumulative_return=(equity_curve[-1][1] - initial_capital) / initial_capital
                          if equity_curve else 0.0,
        annual_return=0.0, sharpe=None, sortino=None, max_drawdown=0.0,
        calmar=None, win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
        daily_equity_curve=equity_curve,
        excess_vs_spy=0.0,
        per_strategy_stats={},
        bid_history=all_bid_records,
    )
