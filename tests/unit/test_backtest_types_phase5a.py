"""Phase 5a new dataclasses — frozen, value-equal, correct field order."""
from dataclasses import FrozenInstanceError
from datetime import date

import pytest


def _result_kwargs(**overrides):
    """Minimum-viable StrategyBacktestResult kwargs (unchanged from Phase 4)."""
    base = {
        "strategy": "momentum_breakout",
        "display_name": "动量突破",
        "horizon": 5,
        "n_trades": 10,
        "n_capacity_skipped": 0,
        "cumulative_return": 0.05,
        "annual_return": 0.12,
        "sharpe": 1.2,
        "sortino": 1.5,
        "max_drawdown": -0.08,
        "calmar": 1.5,
        "win_rate": 0.6,
        "avg_win_pct": 0.03,
        "avg_loss_pct": -0.02,
        "daily_equity_curve": [(date(2026, 4, 1), 10000.0), (date(2026, 5, 1), 10500.0)],
        "excess_vs_spy": 0.02,
    }
    base.update(overrides)
    return base


def _contribution_kwargs(**overrides):
    base = {
        "strategy": "momentum_breakout",
        "display_name": "動量突破",
        "n_trades": 5,
        "n_dedup_skipped": 1,
        "n_capacity_skipped": 0,
        "n_cash_short_skipped": 0,
        "n_size_too_small_skipped": 0,  # NEW Phase 5b
        "contribution_pnl": 250.0,
        "avg_exposure": 0.30,
        "avg_bid_weight": 1.4,
        "avg_position_size": 1450.0,  # NEW Phase 5b
        "n_bids": 6,
        "n_floor_hits": 0,
    }
    base.update(overrides)
    return base


def _portfolio_kwargs(**overrides):
    base = {
        "horizon": 5,
        "n_trades": 30,
        "n_dedup_total": 4,
        "avg_capital_utilization": 0.55,
        "max_strategy_exposure": 0.55,  # NEW Phase 5b
        "hhi_concentration": 0.31,       # NEW Phase 5b
        "cumulative_return": 0.12,
        "annual_return": 0.24,
        "sharpe": 1.4,
        "sortino": 1.7,
        "max_drawdown": -0.06,
        "calmar": 4.0,
        "win_rate": 0.65,
        "avg_win_pct": 0.04,
        "avg_loss_pct": -0.02,
        "daily_equity_curve": [(date(2026, 4, 1), 10000.0), (date(2026, 5, 1), 11200.0)],
        "excess_vs_spy": 0.07,
        "per_strategy_stats": {},
        "bid_history": [],
    }
    base.update(overrides)
    return base


def _bid_kwargs(**overrides):
    base = {
        "date": date(2026, 5, 1),
        "strategy": "momentum_breakout",
        "ticker": "AAPL",
        "weight": 1.2,
        "outcome": "won",
        "winner": None,
        "position_size": 1000.0,  # NEW Phase 5b default
    }
    base.update(overrides)
    return base


# ─── StrategyBacktestResult: Phase 5 hooks still accept None (Phase 4 default) ───

def test_strategy_result_phase5_hooks_default_to_none():
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs())
    assert r.strategy_exposure is None
    assert r.capital_bid_score is None


def test_strategy_result_phase5_hooks_can_be_populated():
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs(
        strategy_exposure=0.32, capital_bid_score=1.45,
    ))
    assert r.strategy_exposure == 0.32
    assert r.capital_bid_score == 1.45


# ─── StrategyBacktestArtifacts ───

def test_artifacts_carry_full_equity_curve():
    from marketpulse.backtest.types import StrategyBacktestArtifacts
    curve = [(date(2026, 4, 1), 10000.0), (date(2026, 4, 2), 10050.0)]
    a = StrategyBacktestArtifacts(strategy="momentum_breakout", full_equity_curve=curve)
    assert a.strategy == "momentum_breakout"
    assert a.full_equity_curve == curve


def test_artifacts_is_frozen():
    from marketpulse.backtest.types import StrategyBacktestArtifacts
    a = StrategyBacktestArtifacts(strategy="x", full_equity_curve=[])
    with pytest.raises(FrozenInstanceError):
        a.strategy = "y"


def test_artifacts_full_equity_curve_not_in_result_dto():
    """Spec § 4: separation of concerns — Result is DTO, Artifacts is compute layer."""
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs())
    # The Result DTO must NOT carry full_equity_curve (that's on Artifacts)
    assert not hasattr(r, "full_equity_curve")
    assert not hasattr(r, "_full_equity_curve")


# ─── StrategyContribution ───

def test_contribution_required_fields():
    from marketpulse.backtest.types import StrategyContribution
    c = StrategyContribution(**_contribution_kwargs())
    assert c.strategy == "momentum_breakout"
    assert c.n_trades == 5
    assert c.n_floor_hits == 0


def test_contribution_is_frozen():
    from marketpulse.backtest.types import StrategyContribution
    c = StrategyContribution(**_contribution_kwargs())
    with pytest.raises(FrozenInstanceError):
        c.n_trades = 999


# ─── BidRecord ───

def test_bid_record_outcome_literal_won():
    from marketpulse.backtest.types import BidRecord
    b = BidRecord(**_bid_kwargs())
    assert b.outcome == "won"
    assert b.winner is None


def test_bid_record_dedup_loser_carries_winner():
    from marketpulse.backtest.types import BidRecord
    b = BidRecord(**_bid_kwargs(outcome="dedup_loser", winner="general"))
    assert b.outcome == "dedup_loser"
    assert b.winner == "general"


def test_bid_record_is_frozen():
    from marketpulse.backtest.types import BidRecord
    b = BidRecord(**_bid_kwargs())
    with pytest.raises(FrozenInstanceError):
        b.weight = 999.0


# ─── PortfolioBacktestResult ───

def test_portfolio_result_required_fields():
    from marketpulse.backtest.types import PortfolioBacktestResult
    r = PortfolioBacktestResult(**_portfolio_kwargs())
    assert r.horizon == 5
    assert r.n_trades == 30
    assert r.n_dedup_total == 4
    assert r.avg_capital_utilization == 0.55


def test_portfolio_result_provenance_defaults():
    """Spec § 4: bid_policy and mtm_model carry Phase 5a v0 provenance."""
    from marketpulse.backtest.types import PortfolioBacktestResult
    r = PortfolioBacktestResult(**_portfolio_kwargs())
    assert r.bid_policy == "rolling_sharpe_60d_v0"
    assert r.mtm_model == "linear_interpolation_v0"
    assert r.display_name == "Shared Pool"


def test_portfolio_result_is_frozen():
    from marketpulse.backtest.types import PortfolioBacktestResult
    r = PortfolioBacktestResult(**_portfolio_kwargs())
    with pytest.raises(FrozenInstanceError):
        r.n_trades = 999


def test_portfolio_result_can_carry_per_strategy_stats():
    from marketpulse.backtest.types import PortfolioBacktestResult, StrategyContribution

    contribs = {"momentum_breakout": StrategyContribution(**_contribution_kwargs())}
    r = PortfolioBacktestResult(**_portfolio_kwargs(per_strategy_stats=contribs))
    assert "momentum_breakout" in r.per_strategy_stats
    assert r.per_strategy_stats["momentum_breakout"].n_trades == 5


# ─── Phase 5b extensions ───

def test_bid_record_has_position_size_field():
    """Phase 5b: BidRecord requires position_size (no default)."""
    from datetime import date

    from marketpulse.backtest.types import BidRecord
    b = BidRecord(
        date=date(2026, 5, 1), strategy="x", ticker="AAPL",
        weight=1.0, outcome="won", winner=None,
        position_size=1500.0,
    )
    assert b.position_size == 1500.0


def test_bid_record_size_too_small_outcome_literal():
    """Phase 5b: new 'size_too_small' outcome in the literal."""
    from datetime import date

    from marketpulse.backtest.types import BidRecord
    b = BidRecord(
        date=date(2026, 5, 1), strategy="x", ticker="AAPL",
        weight=0.5, outcome="size_too_small", winner=None,
        position_size=42.0,  # raw pre-clamp diagnostic value
    )
    assert b.outcome == "size_too_small"
    assert b.position_size == 42.0


def test_strategy_contribution_has_size_telemetry_fields():
    """Phase 5b adds n_size_too_small_skipped + avg_position_size."""
    from marketpulse.backtest.types import StrategyContribution
    c = StrategyContribution(
        strategy="x", display_name="X",
        n_trades=5, n_dedup_skipped=1,
        n_capacity_skipped=0, n_cash_short_skipped=0,
        n_size_too_small_skipped=2,
        contribution_pnl=250.0,
        avg_exposure=0.25, avg_bid_weight=1.2,
        avg_position_size=1450.0,
        n_bids=8, n_floor_hits=0,
    )
    assert c.n_size_too_small_skipped == 2
    assert c.avg_position_size == 1450.0


def test_portfolio_result_has_concentration_telemetry():
    """Phase 5b adds max_strategy_exposure + hhi_concentration + sizing_policy."""
    from datetime import date

    from marketpulse.backtest.types import PortfolioBacktestResult
    r = PortfolioBacktestResult(
        horizon=5,
        n_trades=20, n_dedup_total=3,
        avg_capital_utilization=0.42,
        max_strategy_exposure=0.55,
        hhi_concentration=0.31,
        cumulative_return=0.08, annual_return=0.15,
        sharpe=1.3, sortino=1.6, max_drawdown=-0.04, calmar=3.75,
        win_rate=0.62, avg_win_pct=0.03, avg_loss_pct=-0.018,
        daily_equity_curve=[(date(2026, 4, 1), 10_000.0)],
        excess_vs_spy=0.04,
        per_strategy_stats={},
        bid_history=[],
    )
    assert r.max_strategy_exposure == 0.55
    assert r.hhi_concentration == 0.31
    # sizing_policy default = "fixed_v0" (Phase 5a backward compat)
    assert r.sizing_policy == "fixed_v0"


def test_portfolio_result_sizing_policy_overridable():
    """Phase 5b runs set sizing_policy='vol_target_conviction_v0'."""
    from datetime import date

    from marketpulse.backtest.types import PortfolioBacktestResult
    r = PortfolioBacktestResult(
        horizon=5, n_trades=0, n_dedup_total=0,
        avg_capital_utilization=0.0,
        max_strategy_exposure=0.0, hhi_concentration=0.0,
        cumulative_return=0.0, annual_return=0.0,
        sharpe=None, sortino=None, max_drawdown=0.0, calmar=None,
        win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
        daily_equity_curve=[(date(2026, 4, 1), 10_000.0)],
        excess_vs_spy=0.0,
        per_strategy_stats={}, bid_history=[],
        sizing_policy="vol_target_conviction_v0",
    )
    assert r.sizing_policy == "vol_target_conviction_v0"
