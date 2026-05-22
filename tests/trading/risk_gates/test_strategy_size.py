# Layer: pure
"""6b-T12: StrategySizeGate tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal


def _make_request(
    *,
    strategy="momentum_breakout",
    event_price="150",
    quantity=10,
    risk_intent=None,
):
    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent
    return OrderRequest(
        strategy=strategy, ticker="AAPL", quantity=quantity,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal(event_price),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v1",
        allocator_version="phase6a-v1",
        execution_engine_version="phase6a-v1",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
        risk_intent=risk_intent if risk_intent is not None else RiskIntent.OPEN,
    )


class _StubProvider:
    def __init__(self, mapping):
        self._m = mapping

    def strategy_config(self, name):
        from marketpulse.trading.risk_gates.config_provider import StrategyRiskConfig
        v = self._m.get(name, "MISSING")
        if v == "MISSING":
            return None
        return StrategyRiskConfig(max_position_notional=v)


def _make_gate(mapping):
    from marketpulse.trading.risk_gates.strategy_size import StrategySizeGate
    return StrategySizeGate(provider=_StubProvider(mapping))


def test_strategy_size_close_bypass():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate({"momentum_breakout": Decimal("100")})  # tiny cap
    r = gate.check_pre_trade(
        order_request=_make_request(risk_intent=RiskIntent.CLOSE)
    )
    assert r.approved is True


def test_strategy_size_flip_unsupported():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate({"momentum_breakout": Decimal("25000")})
    r = gate.check_pre_trade(
        order_request=_make_request(risk_intent=RiskIntent.FLIP)
    )
    assert r.approved is False
    assert r.reason == "unsupported_risk_intent"


def test_strategy_size_under_cap_approves():
    gate = _make_gate({"momentum_breakout": Decimal("25000")})
    r = gate.check_pre_trade(
        order_request=_make_request(event_price="150", quantity=10),  # 1500
    )
    assert r.approved is True


def test_strategy_size_at_cap_approves():
    """Op-test #8: proposed == max → APPROVE (deny only on >)."""
    gate = _make_gate({"momentum_breakout": Decimal("1500")})
    r = gate.check_pre_trade(
        order_request=_make_request(event_price="150", quantity=10),  # 1500
    )
    assert r.approved is True


def test_strategy_size_over_cap_denies():
    gate = _make_gate({"momentum_breakout": Decimal("1499")})
    r = gate.check_pre_trade(
        order_request=_make_request(event_price="150", quantity=10),  # 1500
    )
    assert r.approved is False
    assert r.reason == "strategy_size_exceeded"
    assert r.context["proposed"] == "1500"
    assert r.context["limit"] == "1499"
    assert r.context["strategy"] == "momentum_breakout"


def test_strategy_size_missing_strategy_fail_closed():
    """Lock 6b-L9."""
    gate = _make_gate({})  # no entries; provider returns None
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert r.reason == "missing_strategy_risk_config"
    assert r.context["strategy"] == "momentum_breakout"


def test_strategy_size_explicit_none_limit_fail_closed():
    """Lock 6b-L9: strategy_config returns config with None limit."""
    gate = _make_gate({"momentum_breakout": None})
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert r.reason == "missing_strategy_risk_config"
