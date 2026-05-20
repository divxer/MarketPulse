"""Backtest simulator — per-strategy paper portfolio + SPY baseline.

Spec § Open Decision #15: daily loop order is strict CLOSE → OPEN → MTM → RECORD.
Spec § Open Decision #16: causal JOIN constraint enforced in queries module.
Spec § Daily Mark-to-Market: linear interpolation between entry_price and
horizon_price; surfaced as mtm_model='linear_interpolation_v0'.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

from marketpulse.backtest.metrics import compute_metrics
from marketpulse.backtest.queries import EventOutcomePair
from marketpulse.backtest.trading_calendar import (
    build_calendar,
    elapsed_fraction,
)
from marketpulse.backtest.types import (
    StrategyBacktestArtifacts,
    StrategyBacktestResult,
)
from marketpulse.logging import get_logger

log = get_logger(__name__)


@dataclass
class _OpenPosition:
    """Internal simulator state for one in-flight long position."""
    ticker: str
    entry_date: date
    entry_price: float
    horizon_date: date
    horizon_price: float
    position_size: float


def _simulate_strategy_daily(
    pairs: list[EventOutcomePair],
    *,
    position_size: float,
    initial_capital: float,
    max_capital_in_use: float,
) -> tuple[
    list[tuple[date, float]],  # full un-downsampled equity curve
    int,                        # n_trades
    int,                        # n_capacity_skipped
    list[float],                # trade_returns
    list[EventOutcomePair],     # executed_pairs
    list[date],                 # calendar (for upstream metric slicing)
    set[date],                  # db_dates (for upstream metric slicing)
]:
    """Inner shared daily-loop helper. Used by both simulate_strategy_from_pairs
    (Phase 4 entry) and simulate_strategy_with_artifacts (Phase 5a entry).
    Single source of truth for CLOSE/OPEN/MTM/RECORD per-strategy logic.
    """
    db_dates: set[date] = set()
    for p in pairs:
        db_dates.add(p.event_time.date())
        db_dates.add(p.horizon_date)

    raw_dates: set[date] = set(db_dates)
    min_d, max_d = min(raw_dates), max(raw_dates)
    cur = min_d
    while cur <= max_d:
        if cur.weekday() < 5:
            raw_dates.add(cur)
        cur += timedelta(days=1)
    calendar = build_calendar(list(raw_dates))

    pairs_by_entry: dict[date, list[EventOutcomePair]] = {}
    for p in pairs:
        pairs_by_entry.setdefault(p.event_time.date(), []).append(p)

    cash: float = initial_capital
    open_positions: list[_OpenPosition] = []
    n_trades = 0
    n_capacity_skipped = 0
    trade_returns: list[float] = []
    executed_pairs: list[EventOutcomePair] = []
    equity_curve: list[tuple[date, float]] = []

    for d in calendar:
        # CLOSE
        still_open: list[_OpenPosition] = []
        for pos in open_positions:
            if pos.horizon_date == d:
                realized_ret = (pos.horizon_price - pos.entry_price) / pos.entry_price
                cash += pos.position_size * (1 + realized_ret)
                trade_returns.append(realized_ret)
            else:
                still_open.append(pos)
        open_positions = still_open

        # OPEN
        for p in pairs_by_entry.get(d, []):
            capital_in_use = sum(pos.position_size for pos in open_positions)
            if capital_in_use + position_size > max_capital_in_use:
                n_capacity_skipped += 1
                log.info(
                    "backtest_signal_capacity_skipped",
                    ticker=p.ticker, date=d.isoformat(),
                )
                continue
            if cash < position_size:
                n_capacity_skipped += 1
                log.info(
                    "backtest_cash_shortfall_skipped",
                    ticker=p.ticker, date=d.isoformat(), cash=cash,
                )
                continue
            open_positions.append(_OpenPosition(
                ticker=p.ticker, entry_date=d,
                entry_price=p.event_price, horizon_date=p.horizon_date,
                horizon_price=p.horizon_price, position_size=position_size,
            ))
            cash -= position_size
            n_trades += 1
            executed_pairs.append(p)

        # MTM
        positions_value = 0.0
        for pos in open_positions:
            if pos.entry_date == d:
                positions_value += pos.position_size
            else:
                fraction = elapsed_fraction(
                    calendar, entry=pos.entry_date,
                    horizon=pos.horizon_date, current=d,
                )
                est_price = pos.entry_price + (
                    pos.horizon_price - pos.entry_price
                ) * fraction
                positions_value += pos.position_size * (est_price / pos.entry_price)

        # RECORD
        equity_curve.append((d, cash + positions_value))

    return (
        equity_curve, n_trades, n_capacity_skipped,
        trade_returns, executed_pairs, calendar, db_dates,
    )


def simulate_strategy_from_pairs(
    pairs: list[EventOutcomePair],
    *,
    strategy: str,
    display_name: str,
    horizon: int,
    initial_capital: float = 10_000.0,
    position_size: float = 1_000.0,
    max_capital_in_use: float = 10_000.0,
) -> StrategyBacktestResult:
    """Phase 4 isolated-portfolio simulator.

    Public API unchanged. Delegates the daily loop to _simulate_strategy_daily,
    then builds a downsampled curve + StrategyBacktestResult.
    """
    if not pairs:
        return _empty_result(strategy, display_name, horizon, initial_capital)

    (
        equity_curve, n_trades, n_capacity_skipped,
        trade_returns, executed_pairs, _calendar, db_dates,
    ) = _simulate_strategy_daily(
        pairs,
        position_size=position_size,
        initial_capital=initial_capital,
        max_capital_in_use=max_capital_in_use,
    )

    downsampled = downsample_equity_curve(equity_curve, target_points=120)

    metrics_curve = [(d, v) for d, v in equity_curve if d in db_dates]
    metrics = compute_metrics(
        equity_curve=metrics_curve,
        n_trades=n_trades,
        trade_returns=trade_returns,
    )

    return StrategyBacktestResult(
        strategy=strategy,
        display_name=display_name,
        horizon=horizon,
        n_trades=n_trades,
        n_capacity_skipped=n_capacity_skipped,
        cumulative_return=metrics.cumulative_return,
        annual_return=metrics.annual_return,
        sharpe=metrics.sharpe,
        sortino=metrics.sortino,
        max_drawdown=metrics.max_drawdown,
        calmar=metrics.calmar,
        win_rate=metrics.win_rate,
        avg_win_pct=metrics.avg_win_pct,
        avg_loss_pct=metrics.avg_loss_pct,
        daily_equity_curve=downsampled,
        excess_vs_spy=0.0,
    )


def _empty_result(
    strategy: str, display_name: str, horizon: int, initial_capital: float,
) -> StrategyBacktestResult:
    """Result for a strategy with zero bullish events in the window."""
    from datetime import date as _date
    return StrategyBacktestResult(
        strategy=strategy,
        display_name=display_name,
        horizon=horizon,
        n_trades=0,
        n_capacity_skipped=0,
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
    )


def simulate_spy_buyhold(
    pairs: list[EventOutcomePair],
    *,
    initial_capital: float = 10_000.0,
) -> StrategyBacktestResult:
    """SPY buy-and-hold baseline, anchored to the same window as strategy events.

    Spec § SPY Baseline: uses linear interpolation across overlapping
    `benchmark_forward_return` windows from the same outcomes the strategies
    use — methodologically consistent with strategy MTM (both
    mtm_model = 'linear_interpolation_v0').

    Algorithm:
      1. Build calendar from all event/horizon dates in `pairs`.
      2. For each calendar day d, compute the cumulative SPY return:
         - For each outcome o whose [event_date, horizon_date] window covers d,
           add fractional benchmark contribution proportional to
           elapsed_fraction(d) within that window.
         - Average across overlapping windows (simple mean).
      3. equity[d] = initial_capital * (1 + cumulative_spy_return)
    """
    if not pairs:
        from datetime import date as _date
        return StrategyBacktestResult(
            strategy="__spy_buyhold__",
            display_name="SPY 基准",
            horizon=0,
            n_trades=0,
            n_capacity_skipped=0,
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
        )

    raw_dates = []
    for p in pairs:
        raw_dates.append(p.event_time.date())
        raw_dates.append(p.horizon_date)
    calendar = build_calendar(raw_dates)

    equity_curve: list[tuple[date, float]] = []
    for d in calendar:
        contributions: list[float] = []
        for p in pairs:
            entry = p.event_time.date()
            if entry <= d <= p.horizon_date:
                fraction = elapsed_fraction(
                    calendar, entry=entry, horizon=p.horizon_date, current=d,
                )
                contributions.append(p.benchmark_forward_return * fraction)
        spy_ret_to_date = (
            sum(contributions) / len(contributions) if contributions else 0.0
        )
        equity_curve.append((d, initial_capital * (1 + spy_ret_to_date)))

    downsampled = downsample_equity_curve(equity_curve, target_points=120)
    metrics = compute_metrics(
        equity_curve=equity_curve, n_trades=0, trade_returns=[],
    )

    return StrategyBacktestResult(
        strategy="__spy_buyhold__",
        display_name="SPY 基准",
        horizon=0,
        n_trades=0,
        n_capacity_skipped=0,
        cumulative_return=metrics.cumulative_return,
        annual_return=metrics.annual_return,
        sharpe=metrics.sharpe,
        sortino=metrics.sortino,
        max_drawdown=metrics.max_drawdown,
        calmar=metrics.calmar,
        win_rate=0.0,
        avg_win_pct=0.0,
        avg_loss_pct=0.0,
        daily_equity_curve=downsampled,
        excess_vs_spy=0.0,
    )


def downsample_equity_curve(
    curve: list[tuple[date, float]], *, target_points: int = 120,
) -> list[tuple[date, float]]:
    """Reduce a daily equity curve to ~target_points evenly-spaced samples.

    Preserves both endpoints. Used by the simulator before returning a
    StrategyBacktestResult so template contexts stay light.

    Algorithm: take stride = ceil(len/target). Step through the curve at
    that stride, then explicitly append the last point if not already.
    Simple and stable; no statistical sampling needed for visualization.
    """
    n = len(curve)
    if n <= target_points or n <= 2:
        return list(curve)

    stride = max(1, (n + target_points - 1) // target_points)
    out = [curve[i] for i in range(0, n - 1, stride)]
    if out[-1] != curve[-1]:
        out.append(curve[-1])
    return out


def simulate_strategy_with_artifacts(
    pairs: list[EventOutcomePair],
    *,
    strategy: str,
    display_name: str,
    horizon: int,
    initial_capital: float = 10_000.0,
    position_size: float = 1_000.0,
    max_capital_in_use: float = 10_000.0,
) -> tuple[StrategyBacktestResult, StrategyBacktestArtifacts]:
    """Phase 5a variant: returns (DTO, Artifacts) for shared-pool rolling Sharpe.

    Delegates to the shared _simulate_strategy_daily helper, then builds
    BOTH the public DTO and the un-downsampled artifacts side-by-side. No
    duplicated daily loop.
    """
    if not pairs:
        from datetime import date as _date
        result = _empty_result(strategy, display_name, horizon, initial_capital)
        artifacts = StrategyBacktestArtifacts(
            strategy=strategy,
            full_equity_curve=[(_date.today(), initial_capital)],
        )
        return result, artifacts

    (
        equity_curve, n_trades, n_capacity_skipped,
        trade_returns, executed_pairs, _calendar, db_dates,
    ) = _simulate_strategy_daily(
        pairs,
        position_size=position_size,
        initial_capital=initial_capital,
        max_capital_in_use=max_capital_in_use,
    )

    downsampled = downsample_equity_curve(equity_curve, target_points=120)
    metrics_curve = [(d, v) for d, v in equity_curve if d in db_dates]
    metrics = compute_metrics(
        equity_curve=metrics_curve,
        n_trades=n_trades,
        trade_returns=trade_returns,
    )

    result = StrategyBacktestResult(
        strategy=strategy,
        display_name=display_name,
        horizon=horizon,
        n_trades=n_trades,
        n_capacity_skipped=n_capacity_skipped,
        cumulative_return=metrics.cumulative_return,
        annual_return=metrics.annual_return,
        sharpe=metrics.sharpe,
        sortino=metrics.sortino,
        max_drawdown=metrics.max_drawdown,
        calmar=metrics.calmar,
        win_rate=metrics.win_rate,
        avg_win_pct=metrics.avg_win_pct,
        avg_loss_pct=metrics.avg_loss_pct,
        daily_equity_curve=downsampled,
        excess_vs_spy=0.0,
    )
    artifacts = StrategyBacktestArtifacts(
        strategy=strategy,
        full_equity_curve=equity_curve,
    )
    return result, artifacts


def run_all_backtests(
    db,
    *,
    horizon: int = 5,
    since: date | None = None,
    initial_capital: float = 10_000.0,
    position_size: float = 1_000.0,
    max_capital_in_use: float = 10_000.0,
) -> list[StrategyBacktestResult]:
    """Run the 6 Phase 3 strategies + SPY baseline.

    Returns a list ordered: [6 strategies in load_strategies() iteration order,
    then __spy_buyhold__ last]. The /lab/backtest route sorts by Sharpe
    desc itself.
    """
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes
    from marketpulse.strategies import load_strategies

    strategies = load_strategies()
    all_pairs: list[EventOutcomePair] = []
    results: list[StrategyBacktestResult] = []

    for name, strat in strategies.items():
        pairs = get_bullish_events_with_outcomes(
            db, strategy=name, horizon=horizon, since=since,
        )
        all_pairs.extend(pairs)
        r = simulate_strategy_from_pairs(
            pairs=pairs,
            strategy=name,
            display_name=strat.display_name,
            horizon=horizon,
            initial_capital=initial_capital,
            position_size=position_size,
            max_capital_in_use=max_capital_in_use,
        )
        results.append(r)
        log.info(
            "backtest_run_complete",
            strategy=name, horizon=horizon, n_trades=r.n_trades,
            sharpe=r.sharpe, cum_return=r.cumulative_return,
        )

    spy = simulate_spy_buyhold(pairs=all_pairs, initial_capital=initial_capital)

    # Now that SPY's cumulative_return is known, populate excess_vs_spy on
    # each strategy as the actual cum-return diff. This replaces the v0
    # per-trade proxy (which under-reported by position_size/initial_capital)
    # with the same comparison the leaderboard displays elsewhere — i.e.
    #   excess_vs_spy = strategy.cumulative_return - spy.cumulative_return.
    # SPY's own excess_vs_spy stays 0.0 (baseline vs itself).
    results = [
        replace(r, excess_vs_spy=r.cumulative_return - spy.cumulative_return)
        for r in results
    ]

    results.append(spy)
    return results


def run_shared_pool_backtest(
    db,
    *,
    horizon: int = 5,
    since: date | None = None,
    initial_capital: float = 10_000.0,
    position_size: float = 1_000.0,
    max_capital_in_use: float = 10_000.0,
    lookback_days: int = 60,
) -> dict:
    """Phase 5a orchestrator. Spec § 4.

    Returns {isolated, artifacts, shared}:
      - isolated: list[StrategyBacktestResult] — 6 strategies + SPY
      - artifacts: list[StrategyBacktestArtifacts] — parallel to isolated minus SPY
      - shared: PortfolioBacktestResult — Phase 5a combined view
    """
    from dataclasses import dataclass as _dataclass
    from dataclasses import replace

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes
    from marketpulse.strategies import load_strategies

    @_dataclass(frozen=True)
    class _BidInput:
        strategy: str
        ticker: str
        event_time: object
        event_price: float
        horizon_price: float
        horizon_date: object
        forward_return: float
        benchmark_forward_return: float

    strategies = load_strategies()
    isolated: list[StrategyBacktestResult] = []
    artifacts: list = []
    all_bids: list = []
    all_pairs = []

    for name, strat in strategies.items():
        pairs = get_bullish_events_with_outcomes(
            db, strategy=name, horizon=horizon, since=since,
        )
        all_pairs.extend(pairs)
        result, art = simulate_strategy_with_artifacts(
            pairs=pairs,
            strategy=name, display_name=strat.display_name, horizon=horizon,
            initial_capital=initial_capital, position_size=position_size,
            max_capital_in_use=max_capital_in_use,
        )
        isolated.append(result)
        artifacts.append(art)
        for p in pairs:
            all_bids.append(_BidInput(
                strategy=name,
                ticker=p.ticker, event_time=p.event_time,
                event_price=p.event_price, horizon_price=p.horizon_price,
                horizon_date=p.horizon_date,
                forward_return=p.forward_return,
                benchmark_forward_return=p.benchmark_forward_return,
            ))

    # SPY appended last for backward compatibility with run_all_backtests
    spy = simulate_spy_buyhold(pairs=all_pairs, initial_capital=initial_capital)
    isolated.append(spy)

    # Run shared pool, then patch excess_vs_spy
    daily_curves = {a.strategy: a.full_equity_curve for a in artifacts}
    shared = simulate_shared_pool(
        bids=all_bids,
        daily_curves=daily_curves,
        horizon=horizon,
        initial_capital=initial_capital,
        position_size=position_size,
        max_capital_in_use=max_capital_in_use,
        lookback_days=lookback_days,
    )
    shared = replace(
        shared,
        excess_vs_spy=shared.cumulative_return - spy.cumulative_return,
    )

    # Annotate isolated results with Phase 5a hooks (strategy_exposure + capital_bid_score)
    enriched_isolated: list[StrategyBacktestResult] = []
    for r in isolated:
        if r.strategy == "__spy_buyhold__":
            enriched_isolated.append(r)
            continue
        contrib = shared.per_strategy_stats.get(r.strategy)
        if contrib is None:
            enriched_isolated.append(r)
            continue
        enriched_isolated.append(replace(
            r,
            strategy_exposure=contrib.avg_exposure,
            capital_bid_score=contrib.avg_bid_weight,
        ))

    return {
        "isolated": enriched_isolated,
        "artifacts": artifacts,
        "shared": shared,
    }
