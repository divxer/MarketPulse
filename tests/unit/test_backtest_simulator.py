"""Per-strategy portfolio simulator — CLOSE → OPEN → MTM → RECORD daily loop."""
from datetime import UTC, date, datetime

import pytest

from marketpulse.backtest.queries import EventOutcomePair


def _pair(ticker, event_date, event_price, horizon_date, horizon_price,
          benchmark_return=0.01):
    """Helper to construct an EventOutcomePair."""
    return EventOutcomePair(
        ticker=ticker,
        event_time=datetime.combine(event_date, datetime.min.time(), tzinfo=UTC),
        event_price=event_price,
        horizon_price=horizon_price,
        horizon_date=horizon_date,
        forward_return=(horizon_price - event_price) / event_price,
        benchmark_forward_return=benchmark_return,
    )


def test_zero_events_returns_flat_equity_curve_and_zero_trades():
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    r = simulate_strategy_from_pairs(
        pairs=[],
        strategy="momentum_breakout",
        display_name="动量突破",
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    assert r.n_trades == 0
    assert r.cumulative_return == 0.0


def test_single_winning_trade_increases_equity():
    """One bullish event +5% → portfolio value at horizon = 10_050."""
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    pairs = [_pair("AAA", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout",
        display_name="动量突破",
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    assert r.n_trades == 1
    final_val = r.daily_equity_curve[-1][1]
    assert final_val == pytest.approx(10_050.0, abs=1e-3)
    assert r.cumulative_return == pytest.approx(0.005, abs=1e-4)


def test_capital_cap_skips_excess_signals():
    """11 simultaneous $1k bullish events with $10k cap → 10 traded, 1 skipped."""
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    entry = date(2026, 5, 1)
    exit_ = date(2026, 5, 8)
    pairs = [_pair(f"T{i}", entry, 100.0, exit_, 101.0) for i in range(11)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout",
        display_name="动量突破",
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    assert r.n_trades == 10
    assert r.n_capacity_skipped == 1


def test_loop_order_close_before_open_frees_capital_same_day():
    """A position that closes on day D should free capital for a new signal on day D."""
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    pairs = [
        *[_pair(f"A{i}", date(2026, 5, 1), 100.0, date(2026, 5, 4), 101.0) for i in range(10)],
        _pair("B0", date(2026, 5, 4), 100.0, date(2026, 5, 11), 102.0),
    ]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout",
        display_name="动量突破",
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    assert r.n_trades == 11, (
        f"Expected 11 (10 close on 5/4, freeing cap for 11th), got {r.n_trades} "
        f"with {r.n_capacity_skipped} skipped — CLOSE must run before OPEN"
    )
    assert r.n_capacity_skipped == 0


def test_newly_opened_position_does_not_participate_in_same_day_mtm():
    """Open day D: position's est_price == entry_price (fraction = 0)."""
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    pairs = [_pair("X", date(2026, 5, 1), 100.0, date(2026, 5, 8), 110.0)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout", display_name="动量突破", horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    first_day_val = r.daily_equity_curve[0][1]
    assert first_day_val == pytest.approx(10_000.0, abs=1e-6)


def test_mtm_progresses_linearly_during_holding_period():
    """Midpoint of a 4-trading-day hold should reflect ~half the gain."""
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    pairs = [_pair("M", date(2026, 5, 1), 100.0, date(2026, 5, 7), 110.0)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout", display_name="动量突破", horizon=4,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    curve = dict(r.daily_equity_curve)
    mid_val = curve.get(date(2026, 5, 5))
    assert mid_val is not None, f"Expected 2026-05-05 in curve, got {sorted(curve.keys())}"
    assert mid_val == pytest.approx(10_050.0, abs=1.0)


def test_losing_trade_decreases_equity():
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    pairs = [_pair("LOSE", date(2026, 5, 1), 100.0, date(2026, 5, 8), 90.0)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout", display_name="动量突破", horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    final = r.daily_equity_curve[-1][1]
    assert final == pytest.approx(9_900.0, abs=1.0)
    assert r.win_rate == 0.0


def test_simulate_strategy_returns_zero_excess_without_spy_context():
    """simulate_strategy_from_pairs has no SPY to diff against — excess is 0.0.

    The real excess_vs_spy = (strategy.cum_return - spy.cum_return) is
    populated by run_all_backtests() AFTER both runs complete (see the
    integration test in test_backtest_queries.py). At the per-strategy
    unit level the field is meaningless and must be 0.0.
    """
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    pairs = [_pair("X", date(2026, 5, 1), 100.0, date(2026, 5, 8), 110.0,
                    benchmark_return=0.04)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout", display_name="动量突破", horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    assert r.excess_vs_spy == 0.0
    # Strategy itself returned a real cumulative return (+1% on $10k
    # portfolio with one $1k @ +10% trade).
    assert r.cumulative_return > 0


def test_spy_buyhold_single_outcome():
    """One outcome 5/1→5/8 with benchmark_return=0.02 → SPY equity ends ≈ 10_200."""
    from marketpulse.backtest.simulator import simulate_spy_buyhold
    pairs = [_pair("AAA", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0,
                    benchmark_return=0.02)]
    r = simulate_spy_buyhold(
        pairs=pairs,
        initial_capital=10_000.0,
    )
    assert r.strategy == "__spy_buyhold__"
    assert r.display_name == "SPY 基准"
    final = r.daily_equity_curve[-1][1]
    assert final == pytest.approx(10_200.0, abs=10.0)
    assert r.n_trades == 0


def test_spy_buyhold_with_no_pairs_returns_flat():
    from marketpulse.backtest.simulator import simulate_spy_buyhold
    r = simulate_spy_buyhold(pairs=[], initial_capital=10_000.0)
    assert r.cumulative_return == 0.0
    assert r.daily_equity_curve[0][1] == 10_000.0


def test_spy_buyhold_mtm_interpolation_midpoint():
    """5/1 → 5/7 (4 trading days), benchmark 0.04 → midpoint ≈ 10_200."""
    from marketpulse.backtest.simulator import simulate_spy_buyhold
    pairs = [
        _pair("X", date(2026, 5, 1), 100.0, date(2026, 5, 7), 110.0,
              benchmark_return=0.04),
    ]
    pairs.append(_pair("Y", date(2026, 5, 3), 100.0, date(2026, 5, 5), 102.0,
                        benchmark_return=0.0))
    r = simulate_spy_buyhold(pairs=pairs, initial_capital=10_000.0)
    curve = dict(r.daily_equity_curve)
    mid = curve.get(date(2026, 5, 5))
    assert mid is not None
    assert 10_000 <= mid <= curve[date(2026, 5, 7)] + 1


def test_downsample_preserves_curve_under_target():
    """If curve is shorter than target, no downsampling."""
    from datetime import date as _date

    from marketpulse.backtest.simulator import downsample_equity_curve
    curve = [(_date(2026, 5, d), 10_000.0 + d) for d in range(1, 30)]
    out = downsample_equity_curve(curve, target_points=120)
    assert out == curve


def test_downsample_reduces_long_curve_to_target():
    from datetime import date as _date
    from datetime import timedelta as _td

    from marketpulse.backtest.simulator import downsample_equity_curve
    curve = [(_date(2026, 1, 1) + _td(days=d), 10_000.0 + d) for d in range(500)]
    out = downsample_equity_curve(curve, target_points=120)
    assert len(out) <= 122
    assert out[0] == curve[0]
    assert out[-1] == curve[-1]


def test_downsample_endpoints_always_included():
    from datetime import date as _date
    from datetime import timedelta as _td

    from marketpulse.backtest.simulator import downsample_equity_curve
    curve = [(_date(2026, 1, 1) + _td(days=d), float(d)) for d in range(200)]
    out = downsample_equity_curve(curve, target_points=50)
    assert out[0] == curve[0]
    assert out[-1] == curve[-1]


def test_downsample_empty_input_returns_empty():
    from marketpulse.backtest.simulator import downsample_equity_curve
    assert downsample_equity_curve([], target_points=120) == []


def test_downsample_single_point_returns_unchanged():
    from datetime import date as _date

    from marketpulse.backtest.simulator import downsample_equity_curve
    curve = [(_date(2026, 5, 1), 10_000.0)]
    assert downsample_equity_curve(curve, target_points=120) == curve


def test_simulate_strategy_with_artifacts_returns_full_curve():
    """Phase 5a: artifacts variant returns both DTO + un-downsampled curve."""
    from marketpulse.backtest.simulator import simulate_strategy_with_artifacts
    from marketpulse.backtest.types import StrategyBacktestArtifacts
    pairs = [_pair("AAA", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    result, artifacts = simulate_strategy_with_artifacts(
        pairs=pairs,
        strategy="momentum_breakout", display_name="动量突破", horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    assert result.strategy == "momentum_breakout"
    assert isinstance(artifacts, StrategyBacktestArtifacts)
    assert artifacts.strategy == "momentum_breakout"
    assert len(artifacts.full_equity_curve) >= 6


def test_simulate_strategy_with_artifacts_curve_matches_record_step():
    """Artifacts curve is the un-downsampled equity_curve internal to the simulator."""
    from marketpulse.backtest.simulator import simulate_strategy_with_artifacts
    pairs = [_pair("A", date(2026, 5, 1), 100.0, date(2026, 5, 8), 110.0)]
    _, artifacts = simulate_strategy_with_artifacts(
        pairs=pairs, strategy="x", display_name="X", horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    assert artifacts.full_equity_curve[0][1] == pytest.approx(10_000.0, abs=1e-3)
    assert artifacts.full_equity_curve[-1][1] > 10_000.0


def test_simulate_strategy_from_pairs_unchanged_signature():
    """Phase 4 regression: original function still returns single result."""
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    pairs = [_pair("A", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout", display_name="动量突破", horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    assert hasattr(r, "strategy")
    assert not isinstance(r, tuple)
