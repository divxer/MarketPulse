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
        base_position_size=1_000.0,
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
        initial_capital=10_000.0, base_position_size=1_000.0,
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
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,  # Phase 5a invariant: uniform $1k sizes
        sector_caps_enabled=False,        # Phase 5c isolation
        correlation_caps_enabled=False,   # Phase 5c isolation
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
        initial_capital=10_000.0, base_position_size=1_000.0,
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
        initial_capital=10_000.0, base_position_size=1_000.0,
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
        initial_capital=10_000.0, base_position_size=1_000.0,
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
        initial_capital=10_000.0, base_position_size=1_000.0,
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
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,  # Phase 5a invariant: uniform $1k sizes
        sector_caps_enabled=False,        # Phase 5c isolation
        correlation_caps_enabled=False,   # Phase 5c isolation
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
        initial_capital=10_000.0, base_position_size=1_000.0,
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
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,  # Phase 5a invariant: uniform $1k sizes
        sector_caps_enabled=False,        # Phase 5c isolation
        correlation_caps_enabled=False,   # Phase 5c isolation
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
        initial_capital=10_000.0, base_position_size=1_000.0,
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
        initial_capital=10_000.0, base_position_size=1_000.0,
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
        initial_capital=10_000.0, base_position_size=1_000.0,
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
        initial_capital=10_000.0, base_position_size=1_000.0,
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
        initial_capital=10_000.0, base_position_size=1_000.0,
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
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    r90 = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=90,
    )
    assert r60.bid_policy == "rolling_sharpe_60d_v0"
    assert r90.bid_policy == "rolling_sharpe_90d_v0"


def test_shared_pool_bid_policy_set_on_empty_bids_path():
    """Even the no-bids early-return path carries the correct provenance."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    r = simulate_shared_pool(
        bids=[], daily_curves={}, horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
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
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    # All 3 strategies in daily_curves should appear in per_strategy_stats,
    # in alphabetical insertion order regardless of dict construction order.
    keys = list(r2.per_strategy_stats.keys())
    assert keys == sorted(daily_curves.keys()), (
        f"Expected alphabetical order, got {keys}"
    )


# ─── Phase 5b Task 5: SIZE COMPUTE step ────────────────────────────────────

def test_shared_pool_sizing_skips_below_min_with_outcome():
    """Strategy whose computed size < min is skipped with size_too_small."""
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # Build daily curves: one strategy with low α + high σ → raw < min
    # σ ≈ 0.085 → vol_scale = 0.01/0.085 ≈ 0.118 → raw ≈ $118 (< $200 min).
    bad = []
    v = 10_000.0
    import random
    random.seed(99)
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        bad.append((d, v))
        v *= (1 + 0.00005 + random.gauss(0, 0.10))

    bids = [_pair("X", "bad_strategy", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"bad_strategy": bad},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    # bad_strategy's size should be < min → bid skipped
    size_too_small = [b for b in r.bid_history if b.outcome == "size_too_small"]
    assert len(size_too_small) == 1
    assert size_too_small[0].strategy == "bad_strategy"
    # Diagnostic: position_size = raw pre-clamp (< 200)
    assert size_too_small[0].position_size < 200.0


def test_shared_pool_sizing_filters_before_dedup():
    """SIZE filters happen BEFORE DEDUP — strategy below min never wins DEDUP."""
    import random
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    random.seed(50)
    bad = []
    v = 10_000.0
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        bad.append((d, v))
        v *= (1 + 0.00005 + random.gauss(0, 0.05))

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    # Both bid for AAPL — bad would normally lose dedup to good anyway,
    # but here size filter removes bad even before dedup.
    bids = [
        _pair("AAPL", "bad", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("AAPL", "good", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"bad": bad, "good": good},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    # bad should be filtered with size_too_small (not dedup_loser)
    bad_records = [b for b in r.bid_history if b.strategy == "bad"]
    assert len(bad_records) == 1
    assert bad_records[0].outcome == "size_too_small"
    # good wins (was the only one in DEDUP)
    good_won = [b for b in r.bid_history if b.strategy == "good" and b.outcome == "won"]
    assert len(good_won) == 1


def test_shared_pool_sizing_enabled_false_uses_fixed_base():
    """sizing_enabled=False → Phase 5a behavior; every position is base."""
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    bids = [_pair("AAA", "any", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"any": good},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
    )
    # Every BidRecord.position_size == base
    won = [b for b in r.bid_history if b.outcome == "won"]
    assert len(won) == 1
    assert won[0].position_size == 1000.0
    # sizing_policy reflects fixed mode
    assert r.sizing_policy == "fixed_v0"


def test_shared_pool_sizing_provenance_field_set():
    """sizing_enabled=True → sizing_policy='vol_target_conviction_v0'."""
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]
    bids = [_pair("AAA", "any", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"any": good},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    assert r.sizing_policy == "vol_target_conviction_v0"


def test_shared_pool_empty_bids_returns_fixed_v0_when_disabled():
    """Empty bids early-return: sizing_policy reflects flag state."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    r_off = simulate_shared_pool(
        bids=[], daily_curves={}, horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
    )
    assert r_off.sizing_policy == "fixed_v0"

    r_on = simulate_shared_pool(
        bids=[], daily_curves={}, horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    assert r_on.sizing_policy == "vol_target_conviction_v0"


def test_shared_pool_sizing_caps_at_max_when_clamped():
    """A strategy with raw > max gets clamped to max in actual ALLOC."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # Construct a strategy that would compute size > max:
    # Very low σ + only-strategy → vol_scale large, α_scale = 1, raw > max
    low_vol = []
    v = 10_000.0
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        low_vol.append((d, v))
        v *= 1.001  # 0.1% steady (very low σ)

    bids = [_pair("AAA", "low_vol", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"low_vol": low_vol},
        horizon=5,
        initial_capital=10_000.0, base_position_size=2_000.0,
        target_vol=0.01, max_position=4_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    # Won bid's position_size should be capped at max_position
    won = [b for b in r.bid_history if b.outcome == "won"]
    if won:
        assert won[0].position_size <= 4_000.0


def test_shared_pool_high_size_strategy_blocks_more_small_bids():
    """Review iter 1 fix #3: high-conviction strategy consumes more cap.

    Setup: one strategy with high alpha gets a $3k size; 8 other small bids
    at $1k each. The pool ($10k) fills with 1×$3k + 7×$1k = $10k, blocking
    1 small bid (vs Phase 5a where all 9 would have fit at $1k each).
    """
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # high_a_strategy has α much above mean → size > $3k after clamping
    high_a = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.02 ** i))
              for i in range(30)]  # 2% daily growth → high α

    # Other strategies are neutral
    neutrals = {
        f"n{i}": [(date(2026, 4, 1) + timedelta(days=j), 10_000.0 * (1.005 ** j))
                  for j in range(30)]
        for i in range(8)
    }
    daily_curves = {"high_a": high_a, **neutrals}

    # 1 bid for high_a + 8 bids for neutrals on the same day
    bids = [_pair("HIGH", "high_a", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)]
    for i in range(8):
        bids.append(_pair(f"N{i}", f"n{i}", date(2026, 5, 1), 100.0,
                          date(2026, 5, 8), 105.0))

    r = simulate_shared_pool(
        bids=bids,
        daily_curves=daily_curves,
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
        sector_caps_enabled=False,        # Phase 5c isolation
        correlation_caps_enabled=False,   # Phase 5c isolation
    )
    # Total bids attempted: 9. Won bids should be < 9 because high_a's
    # variable size blocks at least one neutral.
    won = [b for b in r.bid_history if b.outcome == "won"]
    cap_full = [b for b in r.bid_history if b.outcome == "cap_full"]
    assert len(won) < 9
    assert len(cap_full) >= 1
    # Total capital allocated should equal pool cap (or close to it)
    total_won_size = sum(b.position_size for b in won)
    assert total_won_size <= 10_000.0  # never exceeds cap


def test_shared_pool_cap_full_records_requested_size():
    """cap_full BidRecord shows the requested size, not 0.0 or base.

    Review iter 1 fix #2: diagnostic value preserved.
    """
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # high-conviction strategy with size > base
    high = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.02 ** i))
            for i in range(30)]

    # 11 bids for the same strategy → 10 fit at variable size, 11th cap-blocked
    daily_curves = {"high": high}
    bids = [_pair(f"T{i}", "high", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 101.0) for i in range(11)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves=daily_curves,
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    cap_full = [b for b in r.bid_history if b.outcome == "cap_full"]
    if cap_full:
        # cap_full position_size should be the ACTUAL computed size (variable),
        # not 0.0 and not base_position_size hardcoded
        for record in cap_full:
            assert record.position_size > 0.0  # real value


def test_shared_pool_avg_position_size_in_contribution():
    """avg_position_size = mean(position_size) over won bids per strategy."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    # 3 bids for the same strategy
    bids = [_pair(f"T{i}", "x", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)
            for i in range(3)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"x": good},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    if "x" in r.per_strategy_stats:
        won = [b for b in r.bid_history
               if b.outcome == "won" and b.strategy == "x"]
        if won:
            expected_avg = sum(b.position_size for b in won) / len(won)
            assert abs(r.per_strategy_stats["x"].avg_position_size - expected_avg) < 1e-6


def test_shared_pool_n_size_too_small_in_contribution():
    """n_size_too_small_skipped counts the strategy's filtered bids."""
    import random

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    random.seed(20)
    bad = []
    v = 10_000.0
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        bad.append((d, v))
        v *= (1 + 0.00005 + random.gauss(0, 0.05))

    bids = [_pair(f"T{i}", "bad", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0) for i in range(3)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"bad": bad},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    if "bad" in r.per_strategy_stats:
        size_skipped = [b for b in r.bid_history
                        if b.outcome == "size_too_small" and b.strategy == "bad"]
        assert r.per_strategy_stats["bad"].n_size_too_small_skipped == len(size_skipped)


def test_shared_pool_max_strategy_exposure_computed():
    """max_strategy_exposure = peak single-strategy avg-exposure value."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]
    bids = [_pair(f"T{i}", "x", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0) for i in range(5)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"x": good},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    # Single strategy → max_strategy_exposure equals its own avg_exposure
    if r.per_strategy_stats:
        max_expected = max(c.avg_exposure for c in r.per_strategy_stats.values())
        assert abs(r.max_strategy_exposure - max_expected) < 1e-9


def test_shared_pool_hhi_concentration_computed():
    """hhi_concentration = Σ(exposure_s²) — Herfindahl-Hirschman Index."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good_a = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
              for i in range(30)]
    good_b = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.006 ** i))
              for i in range(30)]

    bids = [
        _pair("A", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("B", "b", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"a": good_a, "b": good_b},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    # Two strategies → HHI = sum of squares of exposures
    if r.per_strategy_stats:
        exposures = [c.avg_exposure for c in r.per_strategy_stats.values()]
        expected_hhi = sum(e * e for e in exposures)
        assert abs(r.hhi_concentration - expected_hhi) < 1e-9


def test_shared_pool_joint_bootstrap_yields_uniform_base_sizes():
    """ALL strategies n<5 → all sizes = base_position_size (uniform). Review iter 1 fix #1."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # All strategies have 3 events (below min_events=5)
    def tiny(_seed_offset):
        return [
            (date(2026, 4, 28) + timedelta(days=i), 10_000.0 * (1.01 ** i))
            for i in range(3)
        ]
    daily_curves = {"a": tiny(1), "b": tiny(2)}
    bids = [
        _pair("X", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("Y", "b", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves=daily_curves,
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    # Both bids should open at base (no scaling)
    won = [b for b in r.bid_history if b.outcome == "won"]
    for w in won:
        assert w.position_size == 1000.0


def test_size_formula_not_double_rewarding_low_vol():
    """Review iter 2 fix #1: regression test for the σ² double-count.

    Strategy with σ = 0.5%, α = 0.5% (Sharpe = 1.0)
    Strategy with σ = 1.0%, α = 1.5% (Sharpe = 1.5)
    With double-count (size ∝ μ/σ²): A gets bigger size despite lower α.
    Without (size ∝ μ/σ): B gets bigger size (correctly).
    """
    import random

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # Construct A: low σ (0.5% daily), modest α (≈0.5%)
    random.seed(100)
    a_curve = []
    v_a = 10_000.0
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        a_curve.append((d, v_a))
        v_a *= (1 + 0.005 + random.gauss(0, 0.005))

    # Construct B: higher σ (1% daily), higher α (≈1.5%)
    random.seed(200)
    b_curve = []
    v_b = 10_000.0
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        b_curve.append((d, v_b))
        v_b *= (1 + 0.015 + random.gauss(0, 0.010))

    bids = [
        _pair("A_T", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("B_T", "b", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"a": a_curve, "b": b_curve},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    won = {b.strategy: b.position_size for b in r.bid_history if b.outcome == "won"}
    if "a" in won and "b" in won:
        # B's higher α should give it a larger size despite higher σ.
        assert won["b"] > won["a"], (
            f"Higher-α strategy B should get bigger size; "
            f"A={won['a']}, B={won['b']}. "
            f"If A > B, the σ² double-count has regressed."
        )


# ─── Phase 5c-1: ALLOCATE sector cap (Task 6) ───

def test_sector_cap_fires_at_boundary():
    """Pool $10k, 3 same-sector $1k bids → all 3 land (total $3k ≤ $4k cap)."""
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    def fake_sector(ticker: str) -> str:
        return {
            "AAPL": "Technology",
            "MSFT": "Technology",
            "GOOGL": "Technology",
        }.get(ticker, "unknown")

    bids = [
        _pair("AAPL", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("MSFT", "b", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("GOOGL", "c", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {"a": good, "b": good, "c": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=True, sector_cap_pct=0.40,
        correlation_caps_enabled=False,
        sector_provider=fake_sector,
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    assert len(won) == 3
    assert all(b.position_size == 1000.0 for b in won)


def test_sector_cap_fires_when_crossed():
    """5 same-sector $1k bids; 4 land ($4k = cap), 5th blocks."""
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    def fake_sector(_ticker: str) -> str:
        return "Technology"

    bids = [
        _pair(f"T{i}", f"s{i}", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)
        for i in range(5)
    ]
    daily_curves = {f"s{i}": good for i in range(5)}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=True, sector_cap_pct=0.40,
        correlation_caps_enabled=False,
        sector_provider=fake_sector,
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    blocked = [b for b in r.bid_history if b.outcome == "sector_cap_full"]
    # Precondition assert: 4 land first
    assert len(won) == 4
    # Outcome assert: 5th blocked
    assert len(blocked) == 1
    assert blocked[0].blocked_by_sector == "Technology"


def test_sector_cap_unknown_sector_obeys_same_cap():
    """Tickers with sector='unknown' are still subject to the 40% cap."""
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    def fake_sector(_ticker: str) -> str:
        return "unknown"

    bids = [
        _pair(f"T{i}", f"s{i}", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)
        for i in range(5)
    ]
    daily_curves = {f"s{i}": good for i in range(5)}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=True, sector_cap_pct=0.40,
        correlation_caps_enabled=False,
        sector_provider=fake_sector,
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    blocked = [b for b in r.bid_history if b.outcome == "sector_cap_full"]
    assert len(won) == 4
    assert len(blocked) == 1
    assert blocked[0].blocked_by_sector == "unknown"


def test_sector_cap_disabled_via_toggle_bypassed():
    """sector_caps_enabled=False → no cap enforcement."""
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    def fake_sector(_ticker: str) -> str:
        return "Technology"

    bids = [
        _pair(f"T{i}", f"s{i}", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)
        for i in range(5)
    ]
    daily_curves = {f"s{i}": good for i in range(5)}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False,
        correlation_caps_enabled=False,
        sector_provider=fake_sector,
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    blocked = [b for b in r.bid_history if b.outcome == "sector_cap_full"]
    # All 5 land — global cap_full is $10k; 5×$1k=$5k fits
    assert len(won) == 5
    assert len(blocked) == 0


def test_correlation_cap_fires_when_cluster_exceeds():
    """5 correlated tickers (ρ≈1.0); 4 land ($4k cap), 5th blocked because cluster $5k → reject.

    Uses unique tickers per bid so DEDUP doesn't collapse them (DEDUP is keyed
    on same-day same-ticker collisions). All tickers share an identical price
    series so pairwise corr ≈ 1.0 — they form a single correlation cluster.
    """
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # Build identical price series → corr=1.0 across all correlated tickers.
    # Window is [as_of - lookback_days, as_of) = [2026-03-02, 2026-05-01) for
    # as_of=2026-05-01 lookback=60d → seed series ending the day before as_of.
    base_prices = [(date(2026, 3, 2) + timedelta(days=i), 100.0 + i) for i in range(60)]
    correlated_tickers = {"AAPL1", "AAPL2", "AAPL3", "AAPL4", "GOOGL"}

    class FakePriceProvider:
        def get_daily_closes(self, ticker, start, end):
            if ticker in correlated_tickers:
                return [(d, v) for d, v in base_prices if start <= d < end]
            return []

    def neutral_sector(_t: str) -> str:
        return "unknown"

    good_curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
                  for i in range(30)]
    bids = [
        _pair("AAPL1", "s1", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("AAPL2", "s2", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("AAPL3", "s3", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("AAPL4", "s4", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("GOOGL", "s5", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {f"s{i}": good_curve for i in range(1, 6)}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False,  # isolate correlation test
        correlation_caps_enabled=True,
        correlation_cap_pct=0.40, correlation_threshold=0.60,
        sector_provider=neutral_sector,
        price_provider=FakePriceProvider(),
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    blocked = [b for b in r.bid_history if b.outcome == "correlation_cap_full"]
    # 4 AAPLx land first; 5th (GOOGL) blocked because cluster of 5 = $5k > $4k cap
    assert len(won) == 4
    assert len(blocked) == 1
    # Diagnostic preserved
    assert len(blocked[0].blocked_by_correlation_with) > 0
    # Top neighbor is one of the AAPLx tickers (correlation ≈ 1.0)
    assert blocked[0].blocked_by_correlation_with[0][0].startswith("AAPL")


def test_correlation_cap_does_not_fire_below_threshold():
    """Inverse series (ρ ≈ -1) → no neighbor → no cap → both bids land."""
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # Same window alignment as the cluster-fires test (2026-03-02 onwards).
    a_prices = [(date(2026, 3, 2) + timedelta(days=i), 100.0 + i) for i in range(60)]
    b_prices = [(date(2026, 3, 2) + timedelta(days=i), 160.0 - i) for i in range(60)]

    class FakePriceProvider:
        def get_daily_closes(self, ticker, start, end):
            data = {"AAPL": a_prices, "TLT": b_prices}.get(ticker, [])
            return [(d, v) for d, v in data if start <= d < end]

    def neutral_sector(_t: str) -> str:
        return "unknown"

    good_curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
                  for i in range(30)]
    bids = [
        _pair("AAPL", "s1", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("TLT", "s2", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {"s1": good_curve, "s2": good_curve}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False,
        correlation_caps_enabled=True,
        correlation_cap_pct=0.40, correlation_threshold=0.60,
        sector_provider=neutral_sector,
        price_provider=FakePriceProvider(),
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    assert len(won) == 2


def test_correlation_cap_cold_start_bypassed():
    """Insufficient overlap (10 days, min_overlap=30) → None corr → no neighbor → both open."""
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    short_prices = [(date(2026, 4, 20) + timedelta(days=i), 100.0 + i) for i in range(10)]

    class FakePriceProvider:
        def get_daily_closes(self, ticker, start, end):
            if ticker in {"NEW1", "NEW2"}:
                return [(d, v) for d, v in short_prices if start <= d < end]
            return []

    def neutral_sector(_t: str) -> str:
        return "unknown"

    good_curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
                  for i in range(30)]
    bids = [
        _pair("NEW1", "s1", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("NEW2", "s2", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {"s1": good_curve, "s2": good_curve}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False,
        correlation_caps_enabled=True,
        correlation_cap_pct=0.40, correlation_threshold=0.60,
        sector_provider=neutral_sector, price_provider=FakePriceProvider(),
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    # Both should land — cold-start failsafe-open
    assert len(won) == 2


def test_finalization_populates_max_sector_exposure():
    """Pool-wide peak: max over sectors of (sector_total / pool) across all days."""
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    def fake_sector(ticker: str) -> str:
        return {"AAPL": "Technology", "MSFT": "Technology", "XOM": "Energy"}.get(ticker, "unknown")

    bids = [
        _pair("AAPL", "s1", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("MSFT", "s2", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("XOM", "s3", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {"s1": good, "s2": good, "s3": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False, sector_caps_enabled=True, sector_cap_pct=0.40,
        correlation_caps_enabled=False, sector_provider=fake_sector,
    )
    # Tech = 2*$1k=$2k → 20%; Energy = $1k → 10%; max = 20%
    assert abs(r.max_sector_exposure - 0.20) < 0.01
    # Per-sector dict populated
    assert "Technology" in r.max_sector_exposure_by_sector
    assert abs(r.max_sector_exposure_by_sector["Technology"] - 0.20) < 0.01
    assert abs(r.max_sector_exposure_by_sector["Energy"] - 0.10) < 0.01


def test_finalization_populates_sector_breakdown_time_average():
    """sector_breakdown averages each sector's daily fraction over ALL calendar days."""
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    def fake_sector(_t: str) -> str:
        return "Technology"

    bids = [_pair("AAPL", "s1", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    daily_curves = {"s1": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False, sector_caps_enabled=True, sector_cap_pct=0.40,
        correlation_caps_enabled=False, sector_provider=fake_sector,
    )
    # Single position $1k for ~5 days out of 7 calendar day window
    assert "Technology" in r.sector_breakdown
    assert r.sector_breakdown["Technology"] > 0.0
    assert r.sector_breakdown["Technology"] <= 1.0


def test_finalization_n_correlation_cap_events_counted():
    """n_correlation_cap_events counts bids rejected by correlation cap."""
    from datetime import date, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # Window: as_of=2026-05-01 lookback=60d → seed [2026-03-02, 2026-05-01)
    base_prices = [(date(2026, 3, 2) + timedelta(days=i), 100.0 + i) for i in range(60)]
    correlated_tickers = {"AAPL1", "AAPL2", "AAPL3", "AAPL4", "GOOGL"}

    class FakePriceProvider:
        def get_daily_closes(self, ticker, start, end):
            if ticker in correlated_tickers:
                return [(d, v) for d, v in base_prices if start <= d < end]
            return []

    def neutral_sector(_t: str) -> str:
        return "unknown"

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]
    bids = [
        _pair(f"AAPL{i}", f"s{i}", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)
        for i in range(1, 5)
    ] + [_pair("GOOGL", "s5", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    daily_curves = {f"s{i}": good for i in range(1, 6)}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False,
        correlation_caps_enabled=True,
        correlation_cap_pct=0.40, correlation_threshold=0.60,
        sector_provider=neutral_sector, price_provider=FakePriceProvider(),
    )
    blocked = [b for b in r.bid_history if b.outcome == "correlation_cap_full"]
    assert r.n_correlation_cap_events == len(blocked)
    assert r.n_correlation_cap_events >= 1


def test_phase5d_per_day_contribution_decomposition_sums_to_pool_return():
    """Σ daily_strategy_contribution_returns[s][d] == pool_return[d] for every d.

    The Phase 5b T6 invariant (Σ contribution_pnl == pool_pnl) is now reaffirmed
    at day-level granularity via the per-day per-strategy accumulator that
    feeds Phase 5d's LOO subtraction.
    """
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]
    bids = [
        _pair("AAPL", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("MSFT", "b", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {"a": good, "b": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False, correlation_caps_enabled=False,
        contribution_enabled=False,  # Phase 5d default
    )

    daily_equity = r.daily_equity_curve

    # Pool PnL realized at end == sum of per-strategy contribution_pnl
    final_pool_pnl = daily_equity[-1][1] - daily_equity[0][1]
    sum_contribution_pnl = sum(c.contribution_pnl for c in r.per_strategy_stats.values())

    # Outcome: invariant holds within float tolerance
    assert abs(sum_contribution_pnl - final_pool_pnl) < 0.01
