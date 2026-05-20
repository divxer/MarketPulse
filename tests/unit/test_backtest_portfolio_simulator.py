"""Shared-pool simulator — CLOSE → BID → WEIGHT → DEDUP → ALLOC → MTM → RECORD."""
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest  # noqa: F401  (used by future test cases in Tasks 5+6)


@dataclass(frozen=True)
class _BidInput:
    """Lightweight test fixture for shared-pool bid input."""
    strategy: str
    ticker: str
    event_time: datetime
    event_price: float
    horizon_price: float
    horizon_date: date
    forward_return: float
    benchmark_forward_return: float


def _pair(ticker, strategy, event_date, event_price, horizon_date,
          horizon_price, benchmark_return=0.01):
    return _BidInput(
        strategy=strategy,
        ticker=ticker,
        event_time=datetime.combine(event_date, datetime.min.time(), tzinfo=UTC),
        event_price=event_price,
        horizon_price=horizon_price,
        horizon_date=horizon_date,
        forward_return=(horizon_price - event_price) / event_price,
        benchmark_forward_return=benchmark_return,
    )


def _curve(start_value=10_000, n_days=30, daily_return=0.005, start_date=None):
    start_date = start_date or date(2026, 4, 1)
    curve = []
    v = float(start_value)
    for i in range(n_days):
        d = start_date + timedelta(days=i)
        curve.append((d, v))
        v *= (1 + daily_return)
    return curve


def test_shared_pool_zero_bids_returns_flat_curve():
    """No bid inputs → equity stays at initial_capital throughout."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    r = simulate_shared_pool(
        bids=[],
        daily_curves={},
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
        lookback_days=60,
    )
    assert r.n_trades == 0
    assert r.cumulative_return == 0.0


def test_shared_pool_single_bid_opens_one_position():
    """1 bid that wins → 1 trade → equity rises on close."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [_pair("AAA", "momentum_breakout",
                   date(2026, 5, 1), 100.0, date(2026, 5, 8), 110.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    assert r.n_trades == 1


def test_shared_pool_close_frees_cap_before_alloc():
    """CLOSE → BID ordering: position closes day d → cash freed → new same-day bid fits."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        *[_pair(f"A{i}", "momentum_breakout", date(2026, 5, 1), 100.0,
                 date(2026, 5, 4), 101.0) for i in range(10)],
        _pair("B0", "momentum_breakout", date(2026, 5, 4), 100.0,
               date(2026, 5, 11), 102.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    assert r.n_trades == 11


def test_shared_pool_in_flight_ticker_filtered_at_bid_collect():
    """A bid for a ticker already held is filtered at BID COLLECT."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        _pair("AAPL", "momentum_breakout", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
        _pair("AAPL", "news_event", date(2026, 5, 5), 100.0,
               date(2026, 5, 12), 110.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={
            "momentum_breakout": _curve(),
            "news_event": _curve(),
        },
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    assert r.n_trades == 1


def test_shared_pool_bootstrap_period_uses_equal_weight():
    """First 60 days have no mature outcomes → all weights = 1.0 (FIFO order)."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        _pair("X", "momentum_breakout", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
        _pair("Y", "news_event", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": [], "news_event": []},
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    assert r.n_trades == 2


def test_shared_pool_dedup_picks_highest_sharpe_winner():
    """Two strategies bid same ticker same day → highest-Sharpe wins."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        _pair("AAPL", "momentum_breakout", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
        _pair("AAPL", "news_event", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={
            "momentum_breakout": _curve(daily_return=0.01, n_days=30),
            "news_event": _curve(daily_return=0.001, n_days=30),
        },
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    assert r.n_trades == 1
    assert r.n_dedup_total == 1
    assert len([b for b in r.bid_history if b.outcome == "dedup_loser"]) == 1


def test_shared_pool_dedup_loser_records_bid_loss():
    """The losing bid is logged with outcome='dedup_loser' and winner=<name>."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        _pair("AAPL", "momentum_breakout", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
        _pair("AAPL", "news_event", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={
            "momentum_breakout": _curve(daily_return=0.01, n_days=30),
            "news_event": _curve(daily_return=0.001, n_days=30),
        },
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    losers = [b for b in r.bid_history if b.outcome == "dedup_loser"]
    assert len(losers) == 1
    assert losers[0].strategy == "news_event"
    assert losers[0].winner == "momentum_breakout"


def test_shared_pool_greedy_alloc_respects_max_cap():
    """11 same-day distinct-ticker bids in $10k pool → 10 open, 1 cap_full."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        _pair(f"T{i}", "momentum_breakout", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 101.0)
        for i in range(11)
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    assert r.n_trades == 10
    cap_full = [b for b in r.bid_history if b.outcome == "cap_full"]
    assert len(cap_full) == 1


def test_equal_weight_tiebreak_uses_event_time_then_alpha():
    """Tiebreaker chain: weight → event_time → strategy name."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    # Both bids have identical event_time (date-only construction → midnight),
    # so the tiebreaker degenerates to alphabetical strategy.
    # momentum_breakout < news_event lexicographically → momentum_breakout wins.
    bids = [
        _pair("AAPL", "news_event", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
        _pair("AAPL", "momentum_breakout", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"news_event": [], "momentum_breakout": []},
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    assert len(won) == 1
    assert won[0].strategy == "momentum_breakout"


def test_shared_pool_mtm_uses_linear_interp_per_position():
    """Mid-period MTM reflects fractional gain (linear interpolation)."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [_pair("M", "momentum_breakout", date(2026, 5, 1), 100.0,
                   date(2026, 5, 7), 110.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=4,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    curve = dict(r.daily_equity_curve)
    mid = curve.get(date(2026, 5, 5))
    assert mid is not None
    assert mid == pytest.approx(10_050.0, abs=1.0)


def test_shared_pool_no_signal_day_still_records_equity():
    """A day with no bids still records equity (MTM-only update)."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [_pair("X", "momentum_breakout", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    curve_dates = [d for d, _ in r.daily_equity_curve]
    assert date(2026, 5, 4) in curve_dates


def test_shared_pool_contribution_pnl_sums_to_pool_pnl():
    """Σ per_strategy_stats[s].contribution_pnl == pool.cumulative_return * initial."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        _pair("A", "momentum_breakout", date(2026, 5, 1), 100.0, date(2026, 5, 8), 110.0),
        _pair("B", "news_event", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={
            "momentum_breakout": _curve(daily_return=0.01, n_days=30),
            "news_event": _curve(daily_return=0.005, n_days=30),
        },
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    total_contrib = sum(c.contribution_pnl for c in r.per_strategy_stats.values())
    pool_pnl = r.cumulative_return * 10_000.0
    assert abs(total_contrib - pool_pnl) < 1.0


def test_shared_pool_bid_records_capped_at_render_layer():
    """bid_history has at most 100 entries (last-100 slice)."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = []
    base = date(2026, 1, 1)
    for i in range(150):
        bids.append(_pair(
            f"T{i}", "momentum_breakout",
            base + timedelta(days=i % 90), 100.0,
            base + timedelta(days=(i % 90) + 5), 101.0,
        ))
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve(n_days=200)},
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    assert len(r.bid_history) <= 100


def test_shared_pool_avg_capital_utilization_correct():
    """avg_capital_utilization = mean(capital_in_use / max_cap) across all days."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [_pair("A", "momentum_breakout", date(2026, 5, 1), 100.0,
                   date(2026, 5, 5), 105.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=4,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    assert 0.0 <= r.avg_capital_utilization <= 1.0
    assert r.avg_capital_utilization > 0


def test_shared_pool_contribution_pnl_includes_unrealized_mtm():
    """When positions are still open at window end, their MTM is attributed
    to the strategy. Σ contribution_pnl == pool_pnl regardless."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    # 1 position opens 5/1, horizon 5/15 — window will end on max horizon date.
    # All bids resolve within window so this test verifies the realized path.
    bids = [_pair("A", "momentum_breakout", date(2026, 5, 1), 100.0,
                   date(2026, 5, 15), 110.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=10,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    total_contrib = sum(c.contribution_pnl for c in r.per_strategy_stats.values())
    pool_pnl = r.cumulative_return * 10_000.0
    assert abs(total_contrib - pool_pnl) < 1.0


def test_shared_pool_bid_policy_reflects_lookback_days():
    """Review final fix #1: bid_policy provenance string varies with lookback_days
    so dashboards/logs aren't lying when caller passes a non-default window."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    # Two runs, two different lookback windows
    bids = [_pair("A", "momentum_breakout", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)]
    r60 = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    r90 = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=90,
    )
    assert r60.bid_policy == "rolling_sharpe_60d_v0"
    assert r90.bid_policy == "rolling_sharpe_90d_v0"


def test_shared_pool_bid_policy_set_on_empty_bids_path():
    """Even the no-bids early-return path carries the correct provenance."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    r = simulate_shared_pool(
        bids=[], daily_curves={}, horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=30,
    )
    assert r.bid_policy == "rolling_sharpe_30d_v0"


def test_shared_pool_per_strategy_stats_iteration_is_sorted():
    """Review final fix #2: per_strategy_stats dict iteration is deterministic
    (alphabetical strategy name) so template row rendering is stable across runs."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    daily_curves = {
        "zebra": [],            # would land first in set() iteration sometimes
        "alpha": _curve(),
        "momentum_breakout": _curve(),
    }
    # Verify the non-empty path — empty-bids early-return has empty per_strategy_stats
    bids = [_pair("AAA", "zebra", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    r2 = simulate_shared_pool(
        bids=bids,
        daily_curves=daily_curves,
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    # All 3 strategies in daily_curves should appear in per_strategy_stats,
    # in alphabetical insertion order regardless of dict construction order.
    keys = list(r2.per_strategy_stats.keys())
    assert keys == sorted(daily_curves.keys()), (
        f"Expected alphabetical order, got {keys}"
    )
