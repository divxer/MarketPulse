# Layer: pure
"""6b-T13: DailyLossGate tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal


def _make_request(*, allocation_date=date(2026, 5, 21), risk_intent=None):
    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent
    return OrderRequest(
        strategy="momentum_breakout", ticker="AAPL", quantity=10,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=allocation_date,
        event_price=Decimal("150"),
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


class _StubRepo:
    def __init__(self, today_pnl):
        self._pnl = today_pnl
        self.calls = []

    def today_realized_pnl(self, *, tick_date):
        self.calls.append(tick_date)
        return self._pnl


def _make_gate(*, today_pnl, limit=Decimal("500"), enabled=True):
    from marketpulse.trading.risk_gates.config_provider import DailyLossConfig
    from marketpulse.trading.risk_gates.daily_loss import DailyLossGate
    return DailyLossGate(
        cfg=DailyLossConfig(enabled=enabled, daily_loss_limit=limit),
        repository=_StubRepo(today_pnl),
    )


def test_daily_loss_close_bypass():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate(today_pnl=Decimal("-9999"))
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.CLOSE))
    assert r.approved is True


def test_daily_loss_disabled_passes_through():
    gate = _make_gate(today_pnl=Decimal("-9999"), enabled=False)
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is True


def test_daily_loss_under_limit_approves():
    gate = _make_gate(today_pnl=Decimal("-300"), limit=Decimal("500"))
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is True


def test_daily_loss_at_boundary_denies():
    """Op-test #7: realized == -limit exactly denies."""
    gate = _make_gate(today_pnl=Decimal("-500"), limit=Decimal("500"))
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert r.reason == "daily_loss_limit_exceeded"
    assert r.context["today_realized_pnl"] == "-500"
    assert r.context["daily_loss_limit"] == "500"
    assert r.context["allocation_date"] == "2026-05-21"


def test_daily_loss_over_limit_denies():
    gate = _make_gate(today_pnl=Decimal("-800"), limit=Decimal("500"))
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is False


def test_daily_loss_positive_pnl_approves():
    gate = _make_gate(today_pnl=Decimal("250"), limit=Decimal("500"))
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is True


def test_daily_loss_passes_allocation_date_to_repo():
    gate = _make_gate(today_pnl=Decimal("0"))
    gate.check_pre_trade(order_request=_make_request(allocation_date=date(2026, 5, 22)))
    assert gate._repo.calls == [date(2026, 5, 22)]


def test_daily_loss_flip_unsupported():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate(today_pnl=Decimal("0"))
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.FLIP))
    assert r.approved is False
    assert r.reason == "unsupported_risk_intent"
