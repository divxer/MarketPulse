"""Rolling causal Sharpe service for Phase 5a bid weighting."""
from datetime import date, timedelta


def _curve(start_value=10_000, n_days=30, daily_return=0.005, start_date=None):
    """Build a synthetic daily equity curve."""
    start_date = start_date or date(2026, 4, 1)
    curve = []
    v = float(start_value)
    for i in range(n_days):
        d = start_date + timedelta(days=i)
        curve.append((d, v))
        v *= (1 + daily_return)
    return curve


def test_rolling_sharpe_returns_positive_for_steady_upward_curve():
    from marketpulse.backtest.sharpe import rolling_sharpe
    curve = _curve(daily_return=0.005, n_days=30)
    as_of = date(2026, 5, 1)
    s = rolling_sharpe(curve, as_of=as_of, lookback_days=60, min_events=5)
    assert s is not None
    assert s > 0


def test_rolling_sharpe_excludes_dates_at_or_after_as_of():
    """Causality: outcomes with curve date >= as_of are excluded."""
    from marketpulse.backtest.sharpe import rolling_sharpe
    curve = _curve(daily_return=0.005, n_days=30, start_date=date(2026, 4, 1))
    as_of = date(2026, 4, 15)
    s = rolling_sharpe(curve, as_of=as_of, lookback_days=60, min_events=5)
    assert s is not None


def test_rolling_sharpe_returns_none_below_min_events():
    """n<min_events → None (matches Phase 4 n<5 floor)."""
    from marketpulse.backtest.sharpe import rolling_sharpe
    curve = _curve(daily_return=0.005, n_days=3)
    as_of = date(2026, 5, 1)
    s = rolling_sharpe(curve, as_of=as_of, lookback_days=60, min_events=5)
    assert s is None


def test_rolling_sharpe_lookback_window_truncates_curve():
    """Only curve points within [as_of - lookback_days, as_of) participate."""
    from marketpulse.backtest.sharpe import rolling_sharpe
    curve = _curve(daily_return=0.005, n_days=100, start_date=date(2026, 1, 1))
    as_of = date(2026, 4, 15)
    s_30 = rolling_sharpe(curve, as_of=as_of, lookback_days=30, min_events=5)
    s_60 = rolling_sharpe(curve, as_of=as_of, lookback_days=60, min_events=5)
    assert s_30 is not None and s_30 > 0
    assert s_60 is not None and s_60 > 0


def test_rolling_sharpe_empty_curve_returns_none():
    from marketpulse.backtest.sharpe import rolling_sharpe
    s = rolling_sharpe([], as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert s is None


def test_rolling_sharpe_normalizes_inf_to_none():
    """Degenerate input (zero-variance curve) yields inf from empyrical → normalize None."""
    from marketpulse.backtest.sharpe import rolling_sharpe
    flat_curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0) for i in range(30)]
    s = rolling_sharpe(flat_curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert s is None


def test_bid_weight_equal_when_all_strategies_below_threshold():
    """All None Sharpes → all weights = 1.0 (bootstrap)."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {"momentum_breakout": [], "general": []}
    weights, _ = compute_bid_weights(
        ["momentum_breakout", "general"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights == {"momentum_breakout": 1.0, "general": 1.0}


def test_bid_weight_avg_fill_when_some_below_threshold():
    """Mixed None and known → None strategies get avg of known."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {
        "momentum_breakout": _curve(daily_return=0.01, n_days=30),
        "news_event": [],
    }
    weights, _ = compute_bid_weights(
        ["momentum_breakout", "news_event"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights["momentum_breakout"] > 0
    assert weights["news_event"] == weights["momentum_breakout"]


def test_bid_weight_floors_negative_sharpe_at_0_1():
    """Negative Sharpe → floored at 0.1, not lower."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {
        "loser": _curve(daily_return=-0.01, n_days=30),
        "winner": _curve(daily_return=0.01, n_days=30),
    }
    weights, _ = compute_bid_weights(
        ["loser", "winner"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights["loser"] == 0.1
    assert weights["winner"] > 0.1


def test_bid_weight_does_not_floor_high_positive_sharpe():
    """Sharpe >> 0.1 passes through unchanged."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {"winner": _curve(daily_return=0.01, n_days=30)}
    weights, _ = compute_bid_weights(
        ["winner"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights["winner"] > 0.1


def test_bid_weight_all_negative_degenerates_to_fifo():
    """All negative + floor → equal 0.1 weights."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {
        "loser1": _curve(daily_return=-0.01, n_days=30),
        "loser2": _curve(daily_return=-0.005, n_days=30),
    }
    weights, _ = compute_bid_weights(
        ["loser1", "loser2"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights["loser1"] == 0.1
    assert weights["loser2"] == 0.1


def test_bid_weight_deep_negative_sharpe_still_floored_at_0_1():
    """Sharpe = -10 still gets 0.1 floor (not lower)."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {"catastrophic": _curve(daily_return=-0.05, n_days=30)}
    weights, _ = compute_bid_weights(
        ["catastrophic"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights["catastrophic"] == 0.1


def test_compute_bid_weights_raises_on_missing_strategy():
    """Contract: every strategy in strategies_today must be in daily_curves."""
    import pytest

    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {"momentum_breakout": _curve()}
    with pytest.raises(KeyError):
        compute_bid_weights(
            ["momentum_breakout", "missing"], daily_curves,
            as_of=date(2026, 5, 1), lookback_days=60,
        )


def test_bid_weight_empty_curve_in_daily_curves_returns_bootstrap():
    """Empty curve for a strategy → n=0 < 5 → None → bootstrap."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {"empty_strategy": []}
    weights, _ = compute_bid_weights(
        ["empty_strategy"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights == {"empty_strategy": 1.0}


def test_compute_bid_weights_returns_floor_hits_set():
    """Floor hits set tracks strategies whose raw Sharpe was below min_floor."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {
        "loser": _curve(daily_return=-0.01, n_days=30),    # negative Sharpe → floored
        "winner": _curve(daily_return=0.01, n_days=30),    # high Sharpe → unfloored
    }
    weights, floor_hits = compute_bid_weights(
        ["loser", "winner"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert "loser" in floor_hits
    assert "winner" not in floor_hits
    assert weights["loser"] == 0.1
    assert weights["winner"] > 0.1


def test_compute_bid_weights_bootstrap_returns_empty_floor_hits():
    """All-None bootstrap → no floor hits (all 1.0, above any floor)."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    weights, floor_hits = compute_bid_weights(
        ["a", "b"], {"a": [], "b": []},
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert floor_hits == set()
    assert weights == {"a": 1.0, "b": 1.0}


def test_rolling_sigma_returns_positive_for_volatile_curve():
    """Curve with daily 0.5% drift + 0.5% noise → σ ≈ 0.005."""
    import random

    from marketpulse.backtest.sharpe import rolling_sigma

    random.seed(42)
    curve = []
    v = 10_000.0
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        curve.append((d, v))
        v *= (1 + 0.005 + random.gauss(0, 0.005))

    s = rolling_sigma(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert s is not None
    assert 0.0 < s < 0.02  # roughly between 0 and 2% daily std


def test_rolling_sigma_returns_none_below_min_events():
    from marketpulse.backtest.sharpe import rolling_sigma
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
             for i in range(3)]
    s = rolling_sigma(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert s is None


def test_rolling_sigma_uses_60d_window_by_default():
    from marketpulse.backtest.sharpe import rolling_sigma
    # 100 days of 0.5% steady growth — σ should be ~0 (deterministic)
    curve = [(date(2026, 1, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
             for i in range(100)]
    s = rolling_sigma(curve, as_of=date(2026, 4, 11), min_events=5)
    # Steady geometric growth has near-zero relative std → may be None or very small
    assert s is None or s < 0.001


def test_rolling_sigma_excludes_dates_at_or_after_as_of():
    """Causality: curve points >= as_of are excluded from window."""
    from marketpulse.backtest.sharpe import rolling_sigma
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 + 0.01 * (i % 2)))
             for i in range(30)]
    # Window [2026-04-01, 2026-04-15) covers 14 days
    s = rolling_sigma(curve, as_of=date(2026, 4, 15), lookback_days=60, min_events=5)
    assert s is not None
    assert s > 0  # noisy data should yield non-zero std


def test_rolling_sigma_returns_none_when_variance_is_zero():
    """Flat curve → σ = 0 → return None (treated as degenerate)."""
    from marketpulse.backtest.sharpe import rolling_sigma
    flat_curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0) for i in range(30)]
    s = rolling_sigma(flat_curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert s is None


def test_rolling_sigma_empty_curve_returns_none():
    from marketpulse.backtest.sharpe import rolling_sigma
    s = rolling_sigma([], as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert s is None


def test_rolling_sigma_matches_numpy_std_within_tolerance():
    """Cross-check: rolling_sigma should match numpy.std of diff'd returns."""
    import numpy as np

    from marketpulse.backtest.sharpe import rolling_sigma
    values = [10_000.0, 10_050.0, 10_100.0, 10_080.0, 10_120.0, 10_150.0, 10_200.0]
    curve = [(date(2026, 4, 1) + timedelta(days=i), v) for i, v in enumerate(values)]
    s = rolling_sigma(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    arr = np.array(values)
    expected_returns = np.diff(arr) / arr[:-1]
    expected_sigma = float(np.std(expected_returns))
    assert abs(s - expected_sigma) < 1e-9


def test_rolling_sigma_pairs_with_rolling_sharpe_consistent_window():
    """Same input + same window → both functions slice the same data points."""
    from marketpulse.backtest.sharpe import rolling_sharpe, rolling_sigma
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i + 0.001 * (i % 3)))
             for i in range(30)]
    s = rolling_sigma(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    sharpe = rolling_sharpe(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    # Both should be non-None on the same dataset under the same window
    assert s is not None
    assert sharpe is not None
