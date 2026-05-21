"""End-to-end orchestrator test — DB seed + shared-pool run."""
from datetime import UTC, date, datetime, timedelta

from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


def _seed(db, *, ticker, strategy, days_ago=10, excess=0.03, horizon=5):
    e = EvaluationEvent(
        event_type="ai_analysis", subtype="bullish", ticker=ticker,
        event_time=datetime.now(UTC) - timedelta(days=days_ago),
        event_price=100.0,
        payload={"source": "stock_analysis", "strategy": strategy,
                 "strategy_version": "v1", "prompt_version": "analysis-v4"},
    )
    db.add(e)
    db.flush()
    db.add(EvaluationOutcome(
        event_id=e.id, horizon_trading_days=horizon,
        event_price=100.0, horizon_price=100.0 * (1 + excess + 0.001),
        horizon_date=date.today() - timedelta(days=max(0, days_ago - horizon)),
        forward_return=excess + 0.001,
        benchmark_ticker="SPY", benchmark_forward_return=0.001,
        excess_return=excess,
    ))


def test_run_shared_pool_returns_triple(db_session):
    """Orchestrator returns {isolated, artifacts, shared} dict."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    assert "isolated" in out
    assert "artifacts" in out
    assert "shared" in out


def test_run_shared_pool_isolated_matches_run_all_backtests(db_session):
    """Phase 4 regression: isolated list shape = same as run_all_backtests."""
    from marketpulse.backtest.simulator import (
        run_all_backtests,
        run_shared_pool_backtest,
    )
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    iso = run_all_backtests(db_session, horizon=5)
    out = run_shared_pool_backtest(db_session, horizon=5)
    assert len(out["isolated"]) == len(iso)
    assert [r.strategy for r in out["isolated"]] == [r.strategy for r in iso]


def test_run_shared_pool_artifacts_parallel_to_isolated_minus_spy(db_session):
    """Artifacts list parallel-indexed to isolated[:-1] (drops SPY)."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    isolated_no_spy = [r for r in out["isolated"] if r.strategy != "__spy_buyhold__"]
    assert len(out["artifacts"]) == len(isolated_no_spy)
    for art, res in zip(out["artifacts"], isolated_no_spy, strict=True):
        assert art.strategy == res.strategy


def test_run_shared_pool_excess_vs_spy_is_pool_cum_minus_spy_cum(db_session):
    """Orchestrator overrides shared.excess_vs_spy with combined - SPY."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    for i in range(3):
        _seed(db_session, ticker=f"T{i}", strategy="momentum_breakout", excess=0.05)
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    spy = next(r for r in out["isolated"] if r.strategy == "__spy_buyhold__")
    expected = out["shared"].cumulative_return - spy.cumulative_return
    assert abs(out["shared"].excess_vs_spy - expected) < 1e-9


def test_run_shared_pool_with_sizing_enabled_default_true(db_session):
    """Orchestrator defaults sizing_enabled=True (Phase 5b is default)."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    assert out["shared"].sizing_policy == "vol_target_conviction_v0"


def test_run_shared_pool_with_sizing_disabled_yields_phase5a_behavior(db_session):
    """Orchestrator sizing_enabled=False → fixed_v0 (regression mode)."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5, sizing_enabled=False)
    assert out["shared"].sizing_policy == "fixed_v0"


def test_run_shared_pool_sizing_policy_provenance(db_session):
    """sizing_policy strings match the locked decisions in spec § 8."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out_on = run_shared_pool_backtest(db_session, horizon=5, sizing_enabled=True)
    out_off = run_shared_pool_backtest(db_session, horizon=5, sizing_enabled=False)
    assert out_on["shared"].sizing_policy == "vol_target_conviction_v0"
    assert out_off["shared"].sizing_policy == "fixed_v0"


def test_run_shared_pool_default_caps_enabled(db_session):
    """Orchestrator defaults to caps enabled (Phase 5c is default)."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    assert out["shared"].sector_caps_enabled is True
    assert out["shared"].correlation_caps_enabled is True
    assert out["shared"].risk_policy == "cap40_corr06_enforced_v0"


def test_run_shared_pool_caps_disabled_via_kwargs(db_session):
    """Both caps disabled → risk_policy = 'caps_disabled_v0'."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(
        db_session, horizon=5,
        sector_caps_enabled=False,
        correlation_caps_enabled=False,
    )
    assert out["shared"].sector_caps_enabled is False
    assert out["shared"].correlation_caps_enabled is False
    assert out["shared"].risk_policy == "caps_disabled_v0"


def test_run_shared_pool_sector_breakdown_populated(db_session):
    """sector_breakdown field is a dict, even when empty."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    assert isinstance(out["shared"].sector_breakdown, dict)
    assert isinstance(out["shared"].max_sector_exposure_by_sector, dict)


def test_run_shared_pool_default_contribution_disabled(db_session):
    """Default kwargs → contribution_enabled is False, bid_policy is Phase 5a string."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest

    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    assert out["shared"].contribution_enabled is False
    assert out["shared"].bid_policy == "rolling_sharpe_60d_v0"
    assert out["shared"].contribution_policy == "contribution_adjusted_sharpe_60d_v0"


def test_run_shared_pool_contribution_enabled_provenance(db_session):
    """contribution_enabled=True + non-default lambda threads through."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest

    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(
        db_session, horizon=5,
        contribution_enabled=True,
        contribution_lambda=0.7,
    )
    assert out["shared"].contribution_enabled is True
    assert out["shared"].contribution_lambda == 0.7
    assert out["shared"].bid_policy == "contribution_adjusted_sharpe_60d_v0"


def test_run_shared_pool_avg_pool_corr_populated_when_history_sufficient(db_session):
    """avg_pool_corr is a defined float or None on every StrategyContribution."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest

    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    # Outcome: every StrategyContribution has avg_pool_corr field (None or float)
    for _s, c in out["shared"].per_strategy_stats.items():
        assert c.avg_pool_corr is None or isinstance(c.avg_pool_corr, float)
        assert c.n_would_change_rank >= 0


def test_phase5e_overridden_strategy_respects_eff_min_eff_max(
    db_session, tmp_path,
):
    """# Layer: invariant
    Spec § 8 scenario #20. When a strategy has a YAML sizing override,
    every BidRecord that strategy produces (won bids in particular)
    satisfies eff_min <= position_size <= eff_max.

    Pure post-condition that holds independently of dynamics: the clamp
    envelope is respected.
    """
    import shutil
    from pathlib import Path

    import marketpulse.strategies.loader as loader_mod
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    from marketpulse.strategies.loader import clear_strategy_cache

    # Copy default YAMLs to tmp_path, then write one custom override
    default_dir = Path(__file__).parents[2] / "marketpulse/strategies/definitions"
    for yaml_file in default_dir.glob("*.yaml"):
        shutil.copy(yaml_file, tmp_path / yaml_file.name)
    # Patch ONE strategy with custom sizing
    custom_yaml = (tmp_path / "momentum_breakout.yaml").read_text()
    custom_yaml += """
sizing:
  base_position_size: 500
  min_position: 300
  max_position: 800
"""
    (tmp_path / "momentum_breakout.yaml").write_text(custom_yaml)

    # Re-load strategies from tmp_path by monkey-patching the loader's
    # default directory. Clear the cache to force re-read.
    clear_strategy_cache()
    original_dir = loader_mod._DEFAULT_DIR
    loader_mod._DEFAULT_DIR = tmp_path
    try:
        _seed(db_session, ticker="A1", strategy="momentum_breakout")
        db_session.commit()
        out = run_shared_pool_backtest(db_session, horizon=5)
        # Outcome: every won bid for momentum_breakout has size in [300, 800]
        won_mb_bids = [
            b for b in out["shared"].bid_history
            if b.strategy == "momentum_breakout" and b.outcome == "won"
        ]
        # Non-vacuity guard: ensure the test actually exercises the clamp.
        assert len(won_mb_bids) > 0, (
            "test should produce at least one won bid for momentum_breakout"
        )
        for b in won_mb_bids:
            assert 300.0 <= b.position_size <= 800.0, (
                f"Bid {b!r} violates override envelope [300, 800]"
            )
    finally:
        loader_mod._DEFAULT_DIR = original_dir
        clear_strategy_cache()
