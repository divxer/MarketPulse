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
        "n_sector_cap_skipped": 0,        # NEW Phase 5c
        "n_correlation_cap_skipped": 0,   # NEW Phase 5c
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
        "max_sector_exposure": 0.0,              # NEW Phase 5c
        "max_sector_exposure_by_sector": {},     # NEW Phase 5c
        "sector_breakdown": {},                  # NEW Phase 5c
        "max_neighbor_exposure": 0.0,            # NEW Phase 5c
        "n_correlation_cap_events": 0,           # NEW Phase 5c
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
        n_sector_cap_skipped=0,
        n_correlation_cap_skipped=0,
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
        max_sector_exposure=0.0,
        max_sector_exposure_by_sector={},
        sector_breakdown={},
        max_neighbor_exposure=0.0,
        n_correlation_cap_events=0,
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
        max_sector_exposure=0.0,
        max_sector_exposure_by_sector={},
        sector_breakdown={},
        max_neighbor_exposure=0.0,
        n_correlation_cap_events=0,
        cumulative_return=0.0, annual_return=0.0,
        sharpe=None, sortino=None, max_drawdown=0.0, calmar=None,
        win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
        daily_equity_curve=[(date(2026, 4, 1), 10_000.0)],
        excess_vs_spy=0.0,
        per_strategy_stats={}, bid_history=[],
        sizing_policy="vol_target_conviction_v0",
    )
    assert r.sizing_policy == "vol_target_conviction_v0"


# ─── Phase 5c extensions ───

def test_bid_record_sector_cap_full_outcome_with_diagnostic() -> None:
    """Phase 5c: new 'sector_cap_full' outcome + blocked_by_sector diagnostic."""
    from datetime import date

    from marketpulse.backtest.types import BidRecord

    b = BidRecord(
        date=date(2026, 5, 1), strategy="x", ticker="AAPL",
        weight=1.0, outcome="sector_cap_full", winner=None,
        position_size=1500.0,
        blocked_by_sector="Technology",
    )
    assert b.outcome == "sector_cap_full"
    assert b.blocked_by_sector == "Technology"
    assert b.blocked_by_correlation_with == ()


def test_bid_record_correlation_cap_full_with_diagnostic_tuple() -> None:
    """Phase 5c: 'correlation_cap_full' outcome + blocked_by_correlation_with tuple."""
    from datetime import date

    from marketpulse.backtest.types import BidRecord

    diag = (("AAPL", 0.72), ("GOOGL", 0.68))
    b = BidRecord(
        date=date(2026, 5, 1), strategy="x", ticker="TQQQ",
        weight=1.0, outcome="correlation_cap_full", winner=None,
        position_size=2000.0,
        blocked_by_correlation_with=diag,
    )
    assert b.outcome == "correlation_cap_full"
    assert b.blocked_by_sector is None
    assert b.blocked_by_correlation_with == diag


def test_strategy_contribution_has_cap_skip_counters() -> None:
    """Phase 5c adds n_sector_cap_skipped + n_correlation_cap_skipped."""
    from marketpulse.backtest.types import StrategyContribution

    c = StrategyContribution(
        strategy="x", display_name="X",
        n_trades=5, n_dedup_skipped=1,
        n_capacity_skipped=0, n_cash_short_skipped=0,
        n_size_too_small_skipped=0,
        n_sector_cap_skipped=2,
        n_correlation_cap_skipped=1,
        contribution_pnl=100.0,
        avg_exposure=0.20, avg_bid_weight=1.0,
        avg_position_size=1200.0,
        n_bids=9, n_floor_hits=0,
    )
    assert c.n_sector_cap_skipped == 2
    assert c.n_correlation_cap_skipped == 1


def test_portfolio_result_has_sector_correlation_telemetry() -> None:
    """Phase 5c adds 7 new fields with sensible defaults."""
    from datetime import date

    from marketpulse.backtest.types import PortfolioBacktestResult

    r = PortfolioBacktestResult(
        horizon=5,
        n_trades=10, n_dedup_total=2,
        avg_capital_utilization=0.4,
        max_strategy_exposure=0.3, hhi_concentration=0.2,
        max_sector_exposure=0.35,
        max_sector_exposure_by_sector={"Technology": 0.35, "Energy": 0.10},
        sector_breakdown={"Technology": 0.20, "Energy": 0.05},
        max_neighbor_exposure=0.30,
        n_correlation_cap_events=1,
        cumulative_return=0.05, annual_return=0.10,
        sharpe=1.0, sortino=1.2, max_drawdown=-0.05, calmar=2.0,
        win_rate=0.6, avg_win_pct=0.02, avg_loss_pct=-0.01,
        daily_equity_curve=[(date(2026, 4, 1), 10_000.0)],
        excess_vs_spy=0.02,
        per_strategy_stats={}, bid_history=[],
    )
    assert r.max_sector_exposure == 0.35
    assert r.max_sector_exposure_by_sector["Technology"] == 0.35
    assert r.sector_breakdown["Energy"] == 0.05
    assert r.max_neighbor_exposure == 0.30
    assert r.n_correlation_cap_events == 1
    assert r.sector_cap_policy == "uniform_40pct_v0"
    assert r.correlation_cap_policy == "neighbor_sum_rho06_40pct_v0"
    assert r.sector_caps_enabled is True
    assert r.correlation_caps_enabled is True
    assert r.risk_policy == "cap40_corr06_enforced_v0"


def test_portfolio_result_caps_disabled_provenance() -> None:
    """sector/correlation caps disabled → risk_policy='caps_disabled_v0'."""
    from datetime import date

    from marketpulse.backtest.types import PortfolioBacktestResult

    r = PortfolioBacktestResult(
        horizon=5, n_trades=0, n_dedup_total=0,
        avg_capital_utilization=0.0,
        max_strategy_exposure=0.0, hhi_concentration=0.0,
        max_sector_exposure=0.0,
        max_sector_exposure_by_sector={},
        sector_breakdown={},
        max_neighbor_exposure=0.0,
        n_correlation_cap_events=0,
        cumulative_return=0.0, annual_return=0.0,
        sharpe=None, sortino=None, max_drawdown=0.0, calmar=None,
        win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
        daily_equity_curve=[(date(2026, 4, 1), 10_000.0)],
        excess_vs_spy=0.0,
        per_strategy_stats={}, bid_history=[],
        sector_caps_enabled=False,
        correlation_caps_enabled=False,
        risk_policy="caps_disabled_v0",
    )
    assert r.sector_caps_enabled is False
    assert r.correlation_caps_enabled is False
    assert r.risk_policy == "caps_disabled_v0"


# ─── Phase 5d extensions ───

def test_bid_record_phase5d_fields_have_safe_defaults() -> None:
    """Phase 5d adds 7 fields to BidRecord, all defaulted for backward-compat.

    Phase 5e lock #7 dropped pool_corr_excludes_self — promoted to the
    module-level POOL_CORR_MODE constant in marketpulse.backtest.policy.
    """
    from datetime import date

    from marketpulse.backtest.types import BidRecord

    # Construct with NO Phase 5d kwargs — all fields should default to neutral
    b = BidRecord(
        date=date(2026, 5, 1), strategy="x", ticker="AAPL",
        weight=1.5, outcome="won", winner=None, position_size=1000.0,
    )
    assert b.raw_bid_weight is None
    assert b.pool_corr is None
    assert b.contribution_multiplier == 1.0
    assert b.adjusted_bid_weight is None
    assert b.effective_corr_window == 0
    assert b.rewarded_for_negative_corr is False
    assert b.would_change_rank is False


def test_bid_record_phase5d_fields_populated() -> None:
    """Phase 5d fields accept real values without raising."""
    from datetime import date

    from marketpulse.backtest.types import BidRecord

    b = BidRecord(
        date=date(2026, 5, 1), strategy="x", ticker="AAPL",
        weight=1.275, outcome="won", winner=None, position_size=1000.0,
        raw_bid_weight=1.5,
        pool_corr=0.3,
        contribution_multiplier=0.85,
        adjusted_bid_weight=1.275,
        effective_corr_window=42,
        rewarded_for_negative_corr=False,
        would_change_rank=True,
    )
    assert b.raw_bid_weight == 1.5
    assert b.pool_corr == 0.3
    assert b.contribution_multiplier == 0.85
    assert b.adjusted_bid_weight == 1.275
    assert b.effective_corr_window == 42
    assert b.would_change_rank is True


def test_strategy_contribution_phase5d_fields_have_safe_defaults() -> None:
    """Phase 5d adds 2 fields to StrategyContribution, both defaulted."""
    from marketpulse.backtest.types import StrategyContribution

    c = StrategyContribution(
        strategy="x", display_name="X",
        n_trades=5, n_dedup_skipped=1,
        n_capacity_skipped=0, n_cash_short_skipped=0,
        n_size_too_small_skipped=0,
        n_sector_cap_skipped=0, n_correlation_cap_skipped=0,
        contribution_pnl=100.0, avg_exposure=0.20, avg_bid_weight=1.0,
        avg_position_size=1200.0, n_bids=9, n_floor_hits=0,
    )
    # Defaults
    assert c.avg_pool_corr is None
    assert c.n_would_change_rank == 0


def test_strategy_contribution_phase5d_fields_populated() -> None:
    """Phase 5d StrategyContribution fields accept real values."""
    from marketpulse.backtest.types import StrategyContribution

    c = StrategyContribution(
        strategy="x", display_name="X",
        n_trades=5, n_dedup_skipped=1,
        n_capacity_skipped=0, n_cash_short_skipped=0,
        n_size_too_small_skipped=0,
        n_sector_cap_skipped=0, n_correlation_cap_skipped=0,
        contribution_pnl=100.0, avg_exposure=0.20, avg_bid_weight=1.0,
        avg_position_size=1200.0, n_bids=9, n_floor_hits=0,
        avg_pool_corr=0.42,
        n_would_change_rank=7,
    )
    assert c.avg_pool_corr == 0.42
    assert c.n_would_change_rank == 7


def test_portfolio_result_phase5d_provenance_defaults() -> None:
    """Phase 5d adds 3 PortfolioBacktestResult provenance fields, all defaulted."""
    from datetime import date

    from marketpulse.backtest.types import PortfolioBacktestResult

    r = PortfolioBacktestResult(
        horizon=5, n_trades=0, n_dedup_total=0,
        avg_capital_utilization=0.0,
        max_strategy_exposure=0.0, hhi_concentration=0.0,
        max_sector_exposure=0.0, max_sector_exposure_by_sector={},
        sector_breakdown={}, max_neighbor_exposure=0.0,
        n_correlation_cap_events=0,
        cumulative_return=0.0, annual_return=0.0,
        sharpe=None, sortino=None, max_drawdown=0.0, calmar=None,
        win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
        daily_equity_curve=[(date(2026, 4, 1), 10_000.0)],
        excess_vs_spy=0.0,
        per_strategy_stats={}, bid_history=[],
    )
    # Default contribution provenance — disabled, lambda=0.5, policy string
    assert r.contribution_enabled is False
    assert r.contribution_policy == "contribution_adjusted_sharpe_60d_v0"
    assert r.contribution_lambda == 0.5


def test_phase5e_observability_fields_default_to_zero() -> None:
    """# Layer: invariant
    Spec § 2 lock #14 (structural presence). StrategyContribution gains 2 new
    fields, both defaulted: effective_allocation: float = 0.0,
    rank_drift_from_signal: int = 0.

    Manual construction (test fixture) produces structurally-present but
    semantically-null values — "no run yet" state. Simulator output
    populates real values.
    """
    from marketpulse.backtest.types import StrategyContribution
    c = StrategyContribution(
        strategy="x", display_name="X",
        n_trades=0, n_dedup_skipped=0,
        n_capacity_skipped=0, n_cash_short_skipped=0,
        n_size_too_small_skipped=0,
        n_sector_cap_skipped=0, n_correlation_cap_skipped=0,
        contribution_pnl=0.0, avg_exposure=0.0, avg_bid_weight=0.0,
        avg_position_size=0.0, n_bids=0, n_floor_hits=0,
    )
    # Defaults
    assert c.effective_allocation == 0.0
    assert c.rank_drift_from_signal == 0


def test_phase5e_observability_fields_accept_populated_values() -> None:
    """# Layer: invariant
    Both new fields accept real values (positive float and signed int).
    """
    from marketpulse.backtest.types import StrategyContribution
    c = StrategyContribution(
        strategy="x", display_name="X",
        n_trades=5, n_dedup_skipped=1,
        n_capacity_skipped=0, n_cash_short_skipped=0,
        n_size_too_small_skipped=0,
        n_sector_cap_skipped=0, n_correlation_cap_skipped=0,
        contribution_pnl=100.0, avg_exposure=0.2, avg_bid_weight=1.0,
        avg_position_size=500.0, n_bids=9, n_floor_hits=0,
        effective_allocation=0.42,
        rank_drift_from_signal=-2,
    )
    assert c.effective_allocation == 0.42
    assert c.rank_drift_from_signal == -2
