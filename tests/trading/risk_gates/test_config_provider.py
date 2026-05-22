# Layer: pure
"""6b-T1..T6: RiskIntent + RiskConfigProvider tests."""

from __future__ import annotations


def test_risk_intent_enum_values():
    from marketpulse.trading.types import RiskIntent
    assert RiskIntent.OPEN == "open"
    assert RiskIntent.ADD == "add"
    assert RiskIntent.CLOSE == "close"
    assert RiskIntent.REDUCE == "reduce"
    assert RiskIntent.FLIP == "flip"


def test_risk_intent_is_str_enum():
    from marketpulse.trading.types import RiskIntent
    # StrEnum membership preserves str identity
    assert isinstance(RiskIntent.OPEN, str)


def test_order_request_defaults_risk_intent_to_open():
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent

    req = OrderRequest(
        strategy="momentum_breakout",
        ticker="AAPL",
        quantity=10,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150.00"),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155.00"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v1",
        allocator_version="phase6a-v1",
        execution_engine_version="phase6a-v1",
        weight=1.0,
        raw_bid_weight=1.0,
        pool_corr=0.1,
        contribution_multiplier=1.0,
        adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
    )
    assert req.risk_intent == RiskIntent.OPEN


def test_risk_result_defaults_are_back_compat():
    """6a callers construct RiskResult(approved, reason, gate_name); new
    fields default to () and an empty read-only mapping so the old
    signature still works."""
    from marketpulse.trading.risk_gate import RiskResult
    r = RiskResult(approved=True, reason="", gate_name="x")
    assert r.failed_gates == ()
    assert dict(r.context) == {}


def test_risk_result_full_construction():
    from marketpulse.trading.risk_gate import RiskResult
    r = RiskResult(
        approved=False,
        reason="market_hours_outside_window",
        gate_name="market_hours",
        failed_gates=("market_hours",),
        context={"per_gate": [{"gate_name": "market_hours", "approved": False}]},
    )
    assert r.failed_gates == ("market_hours",)
    assert r.context["per_gate"][0]["approved"] is False


def test_risk_result_context_is_immutable_mapping():
    """Lock 6b-L16: top-level context mutation raises TypeError. Gate
    authors pass plain dicts; __post_init__ wraps in MappingProxyType."""
    from marketpulse.trading.risk_gate import RiskResult
    r = RiskResult(approved=False, reason="x", gate_name="g", context={"a": 1})
    import pytest
    with pytest.raises(TypeError):
        r.context["a"] = 2  # type: ignore[index]
    with pytest.raises(TypeError):
        r.context["new_key"] = 99  # type: ignore[index]


def test_risk_gate_module_reexports_risk_intent():
    """6b-L12 back-compat: callers may still write
    `from marketpulse.trading.risk_gate import RiskIntent`."""
    from marketpulse.trading.risk_gate import RiskIntent as RI1
    from marketpulse.trading.types import RiskIntent as RI2
    assert RI1 is RI2


def test_market_hours_config_construction():
    from datetime import time

    from marketpulse.trading.risk_gates.config_provider import MarketHoursConfig
    c = MarketHoursConfig(
        enabled=True, exchange="XNYS",
        allow_regular_session=True, allow_post_close=True,
        post_close_until=time(18, 0), allow_premarket=False,
    )
    assert c.enabled is True
    assert c.post_close_until == time(18, 0)


def test_daily_loss_config_construction():
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import DailyLossConfig
    c = DailyLossConfig(enabled=True, daily_loss_limit=Decimal("500"))
    assert c.daily_loss_limit == Decimal("500")


def test_sector_exposure_config_construction():
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import SectorExposureConfig
    c = SectorExposureConfig(
        enabled=True, max_sector_exposure_pct=0.35,
        configured_max_capital_in_use=Decimal("10000"),
    )
    assert c.max_sector_exposure_pct == 0.35


def test_risk_gate_config_aggregates_three():
    from datetime import time
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import (
        DailyLossConfig,
        MarketHoursConfig,
        RiskGateConfig,
        SectorExposureConfig,
    )
    cfg = RiskGateConfig(
        market_hours=MarketHoursConfig(
            enabled=True, exchange="XNYS",
            allow_regular_session=True, allow_post_close=True,
            post_close_until=time(18, 0), allow_premarket=False,
        ),
        daily_loss=DailyLossConfig(
            enabled=True, daily_loss_limit=Decimal("500"),
        ),
        sector_exposure=SectorExposureConfig(
            enabled=True, max_sector_exposure_pct=0.35,
            configured_max_capital_in_use=Decimal("10000"),
        ),
    )
    assert cfg.market_hours.enabled is True
    assert cfg.daily_loss.daily_loss_limit == Decimal("500")


def test_strategy_risk_config_optional_limit():
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import StrategyRiskConfig
    c = StrategyRiskConfig(max_position_notional=Decimal("25000"))
    assert c.max_position_notional == Decimal("25000")
    c2 = StrategyRiskConfig(max_position_notional=None)
    assert c2.max_position_notional is None
