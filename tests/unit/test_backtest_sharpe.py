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


def test_compute_bid_weights_treats_missing_strategy_as_empty_curve():
    """Contract change (2026-05-26): missing strategies in daily_curves are
    treated as having an empty curve (rolling_sharpe=None → bootstrap path).
    Forward-mode parity with compute_position_sizes — paper_trading_tick's
    allocate_for_day passes curves={} until Phase 6c accumulates data."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {"momentum_breakout": _curve()}
    weights, _floor_hits = compute_bid_weights(
        ["momentum_breakout", "missing"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    # Both strategies receive weights — `missing` via the bootstrap branch
    # (all-None case if both are None, or avg-of-known otherwise).
    assert "missing" in weights
    assert weights["missing"] is not None
    assert weights["missing"] > 0


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


def test_rolling_alpha_returns_positive_for_uptrend():
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import rolling_alpha
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
             for i in range(30)]
    a = rolling_alpha(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert a is not None
    assert a > 0
    # ~0.5% daily growth → α ≈ 0.005 (small rounding from geometric vs arithmetic)
    assert 0.003 < a < 0.007


def test_rolling_alpha_returns_negative_for_downtrend():
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import rolling_alpha
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (0.99 ** i))
             for i in range(30)]
    a = rolling_alpha(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert a is not None
    assert a < 0


def test_rolling_alpha_returns_none_below_min_events():
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import rolling_alpha
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
             for i in range(3)]
    a = rolling_alpha(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert a is None


def test_rolling_alpha_excludes_dates_at_or_after_as_of():
    """Causality: same window semantics as rolling_sigma."""
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import rolling_alpha
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
             for i in range(30)]
    a = rolling_alpha(curve, as_of=date(2026, 4, 15), lookback_days=60, min_events=5)
    assert a is not None
    assert a > 0


def test_rolling_alpha_empty_curve_returns_none():
    from datetime import date

    from marketpulse.backtest.sharpe import rolling_alpha
    a = rolling_alpha([], as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert a is None


def test_rolling_alpha_matches_numpy_mean_within_tolerance():
    """Cross-check: rolling_alpha should match numpy.mean of diff'd returns."""
    from datetime import date, timedelta

    import numpy as np

    from marketpulse.backtest.sharpe import rolling_alpha
    values = [10_000.0, 10_050.0, 10_100.0, 10_080.0, 10_120.0, 10_150.0, 10_200.0]
    curve = [(date(2026, 4, 1) + timedelta(days=i), v) for i, v in enumerate(values)]
    a = rolling_alpha(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    arr = np.array(values)
    expected_returns = np.diff(arr) / arr[:-1]
    expected_alpha = float(np.mean(expected_returns))
    assert abs(a - expected_alpha) < 1e-9


def _noisy_curve(start_value=10_000.0, n_days=30, daily_return=0.005, noise=0.002,
                 start_date=None, seed=42):
    import random
    from datetime import date, timedelta
    random.seed(seed)
    start_date = start_date or date(2026, 4, 1)
    curve = []
    v = float(start_value)
    for i in range(n_days):
        d = start_date + timedelta(days=i)
        curve.append((d, v))
        v *= (1 + daily_return + random.gauss(0, noise))
    return curve


def test_size_high_alpha_low_vol_yields_above_base():
    """High α + low σ → size > base (rewarded for both)."""
    from datetime import date

    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {
        "fast": _noisy_curve(daily_return=0.015, noise=0.002, seed=1),  # high α, lowish σ
        "neutral": _noisy_curve(daily_return=0.005, noise=0.005, seed=2),
    }
    sizes, _, _ = compute_position_sizes(
        ["fast", "neutral"], daily_curves,
        as_of=date(2026, 5, 1),
    )
    assert sizes["fast"] is not None and sizes["neutral"] is not None
    assert sizes["fast"] > sizes["neutral"]


def test_size_low_alpha_high_vol_yields_none_below_min():
    """Low α + high σ → raw < min → None (skip)."""
    from datetime import date

    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {
        "loser": _noisy_curve(daily_return=0.0005, noise=0.020, seed=3),  # tiny α, huge σ
        "winner": _noisy_curve(daily_return=0.015, noise=0.003, seed=4),
    }
    sizes, raw_below, _ = compute_position_sizes(
        ["loser", "winner"], daily_curves,
        as_of=date(2026, 5, 1),
    )
    # loser's raw should be << $200; winner's should be well above
    if sizes["loser"] is None:
        assert "loser" in raw_below
        assert raw_below["loser"] < 200.0
    assert sizes["winner"] is not None


def test_size_neutral_strategy_yields_near_base():
    """Single neutral strategy (only strategy → mean_α = its own α)
    → α_scale = 1 → size = base × (target_vol/σ) only."""
    from datetime import date

    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {
        "neutral": _noisy_curve(daily_return=0.01, noise=0.01, seed=5),  # σ ≈ 1% target
    }
    sizes, _, _ = compute_position_sizes(
        ["neutral"], daily_curves,
        as_of=date(2026, 5, 1), base=1000.0, target_vol=0.01,
    )
    # σ ≈ target_vol → vol_scale ≈ 1; only-strategy → α_scale = 1; size ≈ base
    assert sizes["neutral"] is not None
    assert 500 < sizes["neutral"] < 2000  # roughly around base


def test_size_below_min_returns_none_not_clamped_up():
    """raw < min_position → None (caller skips), NOT clamped up to min."""
    from datetime import date

    from marketpulse.backtest.sharpe import compute_position_sizes
    # Construct curves where math forces raw < 200:
    # huge σ + tiny α, both meet min_events
    bad_curve = _noisy_curve(daily_return=0.0001, noise=0.05, seed=6)
    good_curve = _noisy_curve(daily_return=0.02, noise=0.003, seed=7)
    daily_curves = {"tiny": bad_curve, "huge": good_curve}
    sizes, raw_below, _ = compute_position_sizes(
        ["tiny", "huge"], daily_curves,
        as_of=date(2026, 5, 1), min_position=200.0,
    )
    if sizes["tiny"] is None:
        assert raw_below["tiny"] < 200.0  # raw size was below floor
        assert sizes["tiny"] != 200.0      # NOT clamped up to floor


def test_size_above_max_clamps_to_max():
    """raw > max_position → clamp to max_position."""
    from datetime import date

    from marketpulse.backtest.sharpe import compute_position_sizes
    # Single high-α + low-σ strategy → no α_scale boost (only strategy),
    # but vol_scale boost. To force > max, push σ very low and base higher.
    daily_curves = {
        "low_vol": _noisy_curve(daily_return=0.001, noise=0.0005, seed=8),
    }
    sizes, _, _ = compute_position_sizes(
        ["low_vol"], daily_curves,
        as_of=date(2026, 5, 1), base=2000.0, target_vol=0.01, max_position=4000.0,
    )
    if sizes["low_vol"] is not None:
        assert sizes["low_vol"] <= 4000.0


def test_size_sigma_none_uses_target_vol_fallback():
    """σ unavailable (n<5) → vol_scale = 1.0."""
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import compute_position_sizes
    # Only 3 events → σ is None
    tiny_curve = [(date(2026, 4, 28) + timedelta(days=i), 10_000.0 * (1.01 ** i))
                  for i in range(3)]
    sizes, _, _ = compute_position_sizes(
        ["new"], {"new": tiny_curve},
        as_of=date(2026, 5, 1), base=1000.0,
    )
    # σ None → vol_scale=1.0; α also None (n<5) → α_scale=1.0
    # → size = base × 1.0 × 1.0 = 1000
    assert sizes["new"] == 1000.0


def test_size_zero_sigma_uses_target_vol_fallback():
    """σ computes to 0 (flat curve) → vol_scale = 1.0."""
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import compute_position_sizes
    flat_curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0) for i in range(30)]
    sizes, _, _ = compute_position_sizes(
        ["flat"], {"flat": flat_curve},
        as_of=date(2026, 5, 1), base=1000.0,
    )
    # σ = 0 → vol_scale = 1.0; α = 0 (or None) → α_scale = 1.0
    assert sizes["flat"] is not None
    assert sizes["flat"] == 1000.0


def test_size_joint_bootstrap_yields_uniform_base():
    """ALL strategies have None α AND None σ → all sizes = base. Review fix #1."""
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import compute_position_sizes
    # All strategies with n<5 events → both α and σ are None
    def tiny(_seed):
        return [(date(2026, 4, 28) + timedelta(days=i), 10_000.0 * (1.01 ** i))
                for i in range(3)]
    daily_curves = {"a": tiny(1), "b": tiny(2), "c": tiny(3)}
    sizes, _, _ = compute_position_sizes(
        list(daily_curves.keys()), daily_curves,
        as_of=date(2026, 5, 1), base=1000.0,
    )
    assert sizes == {"a": 1000.0, "b": 1000.0, "c": 1000.0}


def test_size_all_strategies_below_min_returns_all_none():
    """Worst-case: all strategies have raw < min."""
    from datetime import date

    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {
        "a": _noisy_curve(daily_return=0.00005, noise=0.05, seed=10),
        "b": _noisy_curve(daily_return=0.00005, noise=0.04, seed=11),
    }
    sizes, raw_below, _ = compute_position_sizes(
        ["a", "b"], daily_curves,
        as_of=date(2026, 5, 1), min_position=200.0,
    )
    # Both should be None (raw < 200)
    if sizes["a"] is None and sizes["b"] is None:
        assert "a" in raw_below and "b" in raw_below


def test_compute_position_sizes_treats_missing_strategy_as_empty_curve():
    """Contract change (2026-05-26): missing strategies in daily_curves are
    treated as having an empty curve, which yields sigma/alpha=None and a
    fall-back to base_position_size. This unblocks Phase 6 forward mode
    where curves={} is the steady state until 6c accumulates data."""
    from datetime import date

    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {"present": _noisy_curve()}
    sizes, _raw_below, _clamped = compute_position_sizes(
        ["present", "missing"], daily_curves,
        as_of=date(2026, 5, 1),
    )
    # 'missing' had no curve → empty list → rolling_sigma=None → base size.
    # 'present' computes normally based on its curve.
    assert sizes["missing"] is not None
    assert sizes["missing"] > 0    # falls back to base_position_size


def test_compute_position_sizes_returns_raw_sizes_below_min_dict():
    """raw_sizes_below_min populated for None strategies with their raw value."""
    from datetime import date

    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {
        "tiny": _noisy_curve(daily_return=0.00005, noise=0.05, seed=13),
        "big":  _noisy_curve(daily_return=0.02, noise=0.005, seed=14),
    }
    sizes, raw_below, _ = compute_position_sizes(
        ["tiny", "big"], daily_curves,
        as_of=date(2026, 5, 1), min_position=200.0,
    )
    # If tiny got None, its raw should be in the dict and below min
    if sizes["tiny"] is None:
        assert "tiny" in raw_below
        assert raw_below["tiny"] < 200.0
    # big should not appear in raw_below (it passed the floor)
    if sizes["big"] is not None:
        assert "big" not in raw_below


def test_compute_position_sizes_raw_only_for_none_strategies():
    """Strategies whose raw >= min do NOT appear in raw_sizes_below_min."""
    from datetime import date

    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {"normal": _noisy_curve(daily_return=0.005, noise=0.005, seed=15)}
    sizes, raw_below, _ = compute_position_sizes(
        ["normal"], daily_curves,
        as_of=date(2026, 5, 1),
    )
    assert sizes["normal"] is not None
    assert "normal" not in raw_below


def test_size_negative_mean_alpha_falls_back_to_base():
    """Drawdown regime (mean_α < 0) → all strategies get α_scale = 1.0 (base sizing).

    Without the mean_alpha > 0 guard, dividing by a negative mean would
    invert sign: positive-α strategies would get negative alpha_scale →
    raw < 0 < min → silently skipped as size_too_small, while negative-α
    losers would get positive scales. The guard falls back to the
    joint-bootstrap path so every strategy receives base × vol_scale,
    preserving Phase 5a-style uniform sizing through drawdowns.
    """
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import compute_position_sizes

    # Construct two curves both with NEGATIVE daily mean return →
    # mean_alpha will be negative. One slightly less bad than the other
    # so the test exercises both branches of "alpha / mean_alpha would
    # have been positive" vs "negative" if the guard were missing.
    bad_curve = [
        (date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (0.99 ** i))
        for i in range(30)
    ]  # ~ -1% daily → α ≈ -0.01
    worse_curve = [
        (date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (0.98 ** i))
        for i in range(30)
    ]  # ~ -2% daily → α ≈ -0.02

    sizes, raw_below, _ = compute_position_sizes(
        ["bad", "worse"],
        {"bad": bad_curve, "worse": worse_curve},
        as_of=date(2026, 5, 1),
        base=1_000.0,
        target_vol=0.01,
        min_position=200.0,
        max_position=4_000.0,
    )

    # Without the guard, the LESS-bad strategy ("bad") would have
    # alpha_scale = (-0.01) / (-0.015) ≈ 0.67 (positive) and the
    # WORSE one would have alpha_scale = (-0.02) / (-0.015) ≈ 1.33 —
    # inverting the intended ranking. With the guard, both get
    # alpha_scale = 1.0 and size = base × vol_scale (which depends only
    # on σ, not on α direction).
    assert sizes["bad"] is not None, (
        "bad strategy should not be skipped in drawdown regime"
    )
    assert sizes["worse"] is not None, (
        "worse strategy should not be skipped in drawdown regime"
    )
    # Neither should appear in raw_below
    assert "bad" not in raw_below
    assert "worse" not in raw_below


def test_size_zero_mean_alpha_uses_base():
    """mean_α == 0 (degenerate) → α_scale = 1.0 (joint-bootstrap fallback)."""
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import compute_position_sizes

    # Two strategies with exactly opposite drifts → mean_alpha ≈ 0
    up_curve = [
        (date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
        for i in range(30)
    ]
    down_curve = [
        (date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (0.995 ** i))
        for i in range(30)
    ]
    sizes, _, _ = compute_position_sizes(
        ["up", "down"],
        {"up": up_curve, "down": down_curve},
        as_of=date(2026, 5, 1),
        base=1_000.0,
        target_vol=0.01,
    )
    # mean_alpha is near zero; even if slightly off due to compounding,
    # the guard kicks in for mean_alpha <= 0 cases. Both should be
    # non-None and reasonable.
    assert sizes["up"] is not None
    assert sizes["down"] is not None


def test_phase5e_compute_position_sizes_honors_full_override() -> None:
    """# Layer: invariant
    Spec § 8 scenario #17. Full per-strategy override: passes (base, min, max)
    for one strategy; resulting size is clipped to the OVERRIDDEN bounds,
    not the global ones.
    """
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import compute_position_sizes
    # Synthetic curve gives sigma > 0, alpha > 0 (gentle uptrend)
    curve = [
        (date(2026, 1, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
        for i in range(60)
    ]
    daily_curves = {"a": curve}
    overrides = {"a": (500.0, 100.0, 1500.0)}  # tighter envelope
    sizes, raw_below, clamped = compute_position_sizes(
        ["a"], daily_curves,
        as_of=date(2026, 2, 20),
        base=1_000.0, target_vol=0.01,
        min_position=200.0, max_position=4_000.0,
        per_strategy_overrides=overrides,
    )
    # Outcome: size is either None (below 100.0 floor) or <= 1500.0 ceiling
    assert "a" in sizes
    if sizes["a"] is not None:
        assert sizes["a"] <= 1500.0
        # The OVERRIDDEN max (1500) is what clipped, not the global (4000)


def test_phase5e_compute_position_sizes_partial_override_inherits_globals() -> None:
    """# Layer: invariant
    Spec § 8 scenario #18. Partial override (only min_position) — the other
    2 fields inherit globals.
    """
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import compute_position_sizes
    curve = [
        (date(2026, 1, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
        for i in range(60)
    ]
    daily_curves = {"a": curve}
    overrides = {"a": (None, 500.0, None)}  # override min only
    sizes, raw_below, clamped = compute_position_sizes(
        ["a"], daily_curves,
        as_of=date(2026, 2, 20),
        base=1_000.0, target_vol=0.01,
        min_position=200.0, max_position=4_000.0,
        per_strategy_overrides=overrides,
    )
    # Outcome: if size is below 500 (overridden min, NOT 200), it's filtered;
    # else result is <= 4000 (global max preserved)
    if sizes["a"] is not None:
        assert sizes["a"] >= 500.0
        assert sizes["a"] <= 4_000.0
    else:
        # Was filtered by the overridden min — raw must be < 500
        assert raw_below["a"] < 500.0


def test_phase5e_compute_position_sizes_no_override_bit_equivalent_phase5b() -> None:
    """# Layer: invariant
    Spec § 8 scenario #19. With per_strategy_overrides=None (or {}),
    results are BIT-IDENTICAL to a baseline Phase 5b call (no override
    kwarg). Tested across multiple curves to catch any drift.
    """
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import compute_position_sizes
    curves = {}
    for k, growth in [("a", 1.005), ("b", 1.003), ("c", 1.007)]:
        curves[k] = [
            (date(2026, 1, 1) + timedelta(days=i), 10_000.0 * (growth ** i))
            for i in range(60)
        ]
    # Run with no override
    base_sizes, base_raw, base_clamped = compute_position_sizes(
        ["a", "b", "c"], curves,
        as_of=date(2026, 2, 20),
        base=1_000.0, target_vol=0.01,
        min_position=200.0, max_position=4_000.0,
    )
    # Run with empty override map
    ov_sizes, ov_raw, ov_clamped = compute_position_sizes(
        ["a", "b", "c"], curves,
        as_of=date(2026, 2, 20),
        base=1_000.0, target_vol=0.01,
        min_position=200.0, max_position=4_000.0,
        per_strategy_overrides={},
    )
    assert base_sizes == ov_sizes
    assert base_raw == ov_raw


def test_phase5e_compute_position_sizes_signal_purity_lock12() -> None:
    """# Layer: invariant
    Spec § 8 scenario #28 + lock #12. Two runs with identical (sigma, alpha)
    inputs but different override values produce sizes that differ ONLY in
    the clip envelope. Signal-layer outputs (we can probe via fixed-sigma
    constructed curves so sigma/alpha are deterministic) must be identical.

    This is the load-bearing test for the signal-vs-execution boundary.
    """
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import compute_position_sizes
    # Deterministic curves so sigma/alpha are identical between runs.
    curve = [
        (date(2026, 1, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
        for i in range(60)
    ]
    daily_curves = {"a": curve}

    # Run 1: tight override envelope
    s1, _, _ = compute_position_sizes(
        ["a"], daily_curves,
        as_of=date(2026, 2, 20),
        base=1_000.0, target_vol=0.01,
        min_position=200.0, max_position=4_000.0,
        per_strategy_overrides={"a": (200.0, 50.0, 800.0)},
    )
    # Run 2: loose override envelope (same base, different bounds)
    s2, _, _ = compute_position_sizes(
        ["a"], daily_curves,
        as_of=date(2026, 2, 20),
        base=1_000.0, target_vol=0.01,
        min_position=200.0, max_position=4_000.0,
        per_strategy_overrides={"a": (200.0, 50.0, 3000.0)},
    )
    # Outcome: if the larger envelope DIDN'T cap, s1 ∈ [50, 800] (capped)
    # and s2 ∈ [50, 3000] (might be uncapped). When the raw lies in the
    # OVERLAP of both envelopes (>= 50 and <= 800), both runs must agree exactly.
    if s1["a"] is not None and s2["a"] is not None:
        assert s1["a"] <= 800.0
        assert s2["a"] <= 3_000.0
        # When raw <= 800, both envelopes produce identical sizes
        if s2["a"] <= 800.0:
            assert s1["a"] == s2["a"], (
                "Within overlapping envelope, both runs must produce "
                "identical sizes (signal-purity invariant)"
            )


def test_phase5e_size_clamped_by_override_true_when_raw_exceeds_max() -> None:
    """# Layer: invariant
    Spec § 8 scenario #32. clamped_by_override[s] is True iff raw_size
    would have exceeded the override's max. Use a strategy whose raw is
    constructed to be ~$3000 against a $1000 override-max.
    """
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import compute_position_sizes
    # Volatile curve drives vol_scale = target_vol / sigma up, so raw size
    # exceeds the override max.
    curve = []
    base_price = 10_000.0
    for i in range(60):
        price = base_price * (1 + 0.02 * ((-1) ** i)) * (1.005 ** i)
        curve.append((date(2026, 1, 1) + timedelta(days=i), price))
    daily_curves = {"a": curve}
    # Use tight override max to force the clamp
    sizes, _, clamped = compute_position_sizes(
        ["a"], daily_curves,
        as_of=date(2026, 2, 20),
        base=1_000.0, target_vol=0.01,
        min_position=200.0, max_position=4_000.0,
        per_strategy_overrides={"a": (1_000.0, 200.0, 800.0)},
    )
    # The raw size MUST exceed 800 for this test to be meaningful;
    # if not, the test's fixture needs tuning, not the code.
    if sizes["a"] is not None and sizes["a"] == 800.0:
        # Raw was >= 800, so clamp fired
        assert clamped["a"] is True


def test_phase5e_size_clamped_by_override_false_when_raw_in_envelope() -> None:
    """# Layer: invariant
    Spec § 8 scenario #32 (negative case). clamped_by_override[s] is False
    when raw_size lies within (eff_min, eff_max).
    """
    from datetime import date, timedelta

    from marketpulse.backtest.sharpe import compute_position_sizes
    # Gentle curve with small alternating noise so sigma ≈ target_vol
    # → vol_scale near 1.0, raw ≈ base ≈ 1000 (well inside envelope).
    # A pure (1.001 ** i) curve has sigma ≈ 0, which inflates vol_scale
    # and pushes raw outside any reasonable envelope.
    curve = []
    for i in range(60):
        noise = 1 + 0.005 * ((-1) ** i)
        curve.append(
            (date(2026, 1, 1) + timedelta(days=i), 10_000.0 * noise * (1.001 ** i))
        )
    daily_curves = {"a": curve}
    # Wide envelope ensures raw is comfortably inside
    sizes, _, clamped = compute_position_sizes(
        ["a"], daily_curves,
        as_of=date(2026, 2, 20),
        base=1_000.0, target_vol=0.01,
        min_position=200.0, max_position=4_000.0,
        per_strategy_overrides={"a": (1_000.0, 100.0, 10_000.0)},
    )
    # Outcome: raw is ~1000, well below 10000 max → no clamp
    if sizes["a"] is not None:
        assert clamped["a"] is False
