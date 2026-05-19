"""Backtest simulator — per-strategy paper portfolio + SPY baseline.

Spec § Open Decision #15: daily loop order is strict CLOSE → OPEN → MTM → RECORD.
Spec § Open Decision #16: causal JOIN constraint enforced in queries module.
Spec § Daily Mark-to-Market: linear interpolation between entry_price and
horizon_price; surfaced as mtm_model='linear_interpolation_v0'.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from marketpulse.backtest.metrics import compute_metrics
from marketpulse.backtest.queries import EventOutcomePair
from marketpulse.backtest.trading_calendar import (
    build_calendar,
    elapsed_fraction,
)
from marketpulse.backtest.types import StrategyBacktestResult
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
    """Simulate a long-only paper portfolio for ONE strategy.

    Algorithm (spec § Portfolio Simulator Algorithm):
      For each trading day d in [first_event_date, max_horizon_date]:
        a) CLOSE positions whose horizon_date == d
        b) OPEN new bullish events with event_time.date() == d
        c) MTM open positions opened BEFORE today (linear interpolation)
        d) RECORD equity[d] = cash + Σ position_values

    Returns:
        StrategyBacktestResult with downsampled daily_equity_curve.
    """
    if not pairs:
        return _empty_result(strategy, display_name, horizon, initial_capital)

    raw_dates: set[date] = set()
    for p in pairs:
        raw_dates.add(p.event_time.date())
        raw_dates.add(p.horizon_date)
    # Fill in weekdays between min and max so MTM days appear in the
    # equity curve even when only entry/horizon dates are in the DB
    # (e.g. sparse single-event windows). Calendar still excludes weekends.
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
    equity_curve: list[tuple[date, float]] = []
    executed_pairs: list[EventOutcomePair] = []

    for d in calendar:
        # a) CLOSE
        still_open: list[_OpenPosition] = []
        for pos in open_positions:
            if pos.horizon_date == d:
                realized_ret = (pos.horizon_price - pos.entry_price) / pos.entry_price
                realized_pnl = pos.position_size * realized_ret
                cash += pos.position_size + realized_pnl
                trade_returns.append(realized_ret)
            else:
                still_open.append(pos)
        open_positions = still_open

        # b) OPEN
        for p in pairs_by_entry.get(d, []):
            capital_in_use = sum(pos.position_size for pos in open_positions)
            if capital_in_use + position_size > max_capital_in_use:
                n_capacity_skipped += 1
                log.info(
                    "backtest_signal_capacity_skipped",
                    strategy=strategy, ticker=p.ticker, date=d.isoformat(),
                )
                continue
            if cash < position_size:
                n_capacity_skipped += 1
                log.info(
                    "backtest_cash_shortfall_skipped",
                    strategy=strategy, ticker=p.ticker, date=d.isoformat(),
                    cash=cash,
                )
                continue
            open_positions.append(_OpenPosition(
                ticker=p.ticker,
                entry_date=d,
                entry_price=p.event_price,
                horizon_date=p.horizon_date,
                horizon_price=p.horizon_price,
                position_size=position_size,
            ))
            cash -= position_size
            n_trades += 1
            executed_pairs.append(p)

        # c) MTM
        positions_value = 0.0
        for pos in open_positions:
            if pos.entry_date == d:
                positions_value += pos.position_size
            else:
                fraction = elapsed_fraction(
                    calendar,
                    entry=pos.entry_date,
                    horizon=pos.horizon_date,
                    current=d,
                )
                est_price = pos.entry_price + (
                    pos.horizon_price - pos.entry_price
                ) * fraction
                est_value = pos.position_size * (est_price / pos.entry_price)
                positions_value += est_value

        # d) RECORD
        equity_curve.append((d, cash + positions_value))

    downsampled = downsample_equity_curve(equity_curve, target_points=120)

    metrics = compute_metrics(
        equity_curve=equity_curve,
        n_trades=n_trades,
        trade_returns=trade_returns,
    )

    # Excess vs SPY proxy: avg(forward − benchmark) over executed trades,
    # scaled by deployed-capital fraction.
    excess_terms = [
        p.forward_return - p.benchmark_forward_return for p in executed_pairs
    ]
    excess_vs_spy_proxy = (
        sum(excess_terms) / len(excess_terms) if excess_terms else 0.0
    ) * (position_size / initial_capital)

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
        excess_vs_spy=excess_vs_spy_proxy,
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
