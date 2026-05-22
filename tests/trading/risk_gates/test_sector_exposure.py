# Layer: pure
"""6b-T14: SectorExposureGate tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal


def _make_request(
    *,
    ticker="AAPL",
    event_price="150",
    quantity=10,
    risk_intent=None,
):
    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent
    return OrderRequest(
        strategy="momentum_breakout", ticker=ticker, quantity=quantity,
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


class _StubRepo:
    def __init__(self, current):
        self._current = current

    def sector_exposure_notional(self, *, sector_provider):
        return dict(self._current)


def _make_gate(*, current, sector_map, pct=0.35, denom=Decimal("10000"), enabled=True):
    from marketpulse.trading.risk_gates.config_provider import SectorExposureConfig
    from marketpulse.trading.risk_gates.sector_exposure import SectorExposureGate
    return SectorExposureGate(
        cfg=SectorExposureConfig(
            enabled=enabled, max_sector_exposure_pct=pct,
            configured_max_capital_in_use=denom,
        ),
        repository=_StubRepo(current),
        sector_provider=lambda t: sector_map.get(t),
    )


def test_sector_exposure_close_bypass():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate(current={"Tech": Decimal("99999")}, sector_map={"AAPL": "Tech"})
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.CLOSE))
    assert r.approved is True


def test_sector_exposure_disabled_passes_through():
    gate = _make_gate(
        current={"Tech": Decimal("99999")},
        sector_map={"AAPL": "Tech"},
        enabled=False,
    )
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is True


def test_sector_exposure_unknown_sector_denies_fail_closed():
    """Lock 6b-L8."""
    gate = _make_gate(current={}, sector_map={})  # ticker → None
    r = gate.check_pre_trade(order_request=_make_request(ticker="UNKNOWNTICK"))
    assert r.approved is False
    assert r.reason == "unknown_sector"
    assert r.context["ticker"] == "UNKNOWNTICK"


def test_sector_exposure_under_cap_approves():
    # cap = 0.35 * 10_000 = 3500. current Tech=1000, proposed=1500 → projected 2500 < 3500.
    gate = _make_gate(
        current={"Tech": Decimal("1000")},
        sector_map={"AAPL": "Tech"},
    )
    r = gate.check_pre_trade(order_request=_make_request(event_price="150", quantity=10))
    assert r.approved is True


def test_sector_exposure_at_cap_approves():
    """projected == cap → approve (deny only on >)."""
    # cap = 3500; current=2000; proposed=1500; projected=3500 == cap.
    gate = _make_gate(
        current={"Tech": Decimal("2000")},
        sector_map={"AAPL": "Tech"},
    )
    r = gate.check_pre_trade(order_request=_make_request(event_price="150", quantity=10))
    assert r.approved is True


def test_sector_exposure_over_cap_denies_with_projection_context():
    """Op-test #10: deny with full projection context."""
    # cap = 3500; current=2500; proposed=1500; projected=4000 > 3500.
    gate = _make_gate(
        current={"Tech": Decimal("2500")},
        sector_map={"AAPL": "Tech"},
    )
    r = gate.check_pre_trade(order_request=_make_request(event_price="150", quantity=10))
    assert r.approved is False
    assert r.reason == "sector_cap_exceeded"
    assert r.context["sector"] == "Tech"
    assert r.context["current"] == "2500"
    assert r.context["proposed"] == "1500"
    assert r.context["projected"] == "4000"
    assert r.context["cap"] == "3500.00"


def test_sector_exposure_denominator_fixed_not_live_cash():
    """Op-test #11: live cash doesn't affect the cap denominator.
    Test pegs configured_max_capital_in_use to a fixed Decimal — the
    gate must use that, not anything observed from a repo."""
    gate = _make_gate(
        current={"Tech": Decimal("0")},
        sector_map={"AAPL": "Tech"},
        pct=0.5, denom=Decimal("1000"),  # cap = 500
    )
    # proposed = 150 * 4 = 600 > 500 → deny.
    r = gate.check_pre_trade(order_request=_make_request(event_price="150", quantity=4))
    assert r.approved is False
    assert r.context["cap"] == "500.0"


def test_sector_exposure_flip_unsupported():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate(current={}, sector_map={"AAPL": "Tech"})
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.FLIP))
    assert r.approved is False
    assert r.reason == "unsupported_risk_intent"
