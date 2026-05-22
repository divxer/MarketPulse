# Layer: pure
"""6b-T11: MarketHoursGate tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo


def test_calendar_module_exports_ny_zoneinfo():
    from marketpulse.trading.calendar import NY
    assert NY == ZoneInfo("America/New_York")  # noqa: SIM300


# === Test fixtures ===

NY_TZ = ZoneInfo("America/New_York")


def _make_request(*, allocation_date=date(2026, 5, 21), risk_intent=None, ticker="AAPL"):
    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent
    return OrderRequest(
        strategy="momentum_breakout", ticker=ticker, quantity=10,
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


class _FakeClock:
    def __init__(self, now):
        self._now = now

    def now(self):
        return self._now


def _make_gate(now_ny=None, cfg=None):
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.risk_gates.config_provider import MarketHoursConfig
    from marketpulse.trading.risk_gates.market_hours import MarketHoursGate
    if now_ny is None:
        now_ny = datetime(2026, 5, 21, 14, 0, tzinfo=NY_TZ)
    if cfg is None:
        cfg = MarketHoursConfig(
            enabled=True, exchange="XNYS",
            allow_regular_session=True, allow_post_close=True,
            post_close_until=time(18, 0), allow_premarket=False,
        )
    return MarketHoursGate(
        cfg=cfg, calendar=NYTradingCalendar(),
        clock=_FakeClock(now_ny.astimezone(UTC)),
    )


def test_market_hours_close_bypass():
    """6b-L1: CLOSE intent skips the gate even outside hours."""
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate(now_ny=datetime(2026, 5, 21, 22, 0, tzinfo=NY_TZ))  # 22:00 NY
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.CLOSE))
    assert r.approved is True


def test_market_hours_reduce_bypass():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate(now_ny=datetime(2026, 5, 21, 22, 0, tzinfo=NY_TZ))
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.REDUCE))
    assert r.approved is True


def test_market_hours_flip_unsupported():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate()
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.FLIP))
    assert r.approved is False
    assert r.reason == "unsupported_risk_intent"
    assert r.gate_name == "market_hours"


def test_market_hours_disabled_passes_through():
    from marketpulse.trading.risk_gates.config_provider import MarketHoursConfig
    cfg = MarketHoursConfig(
        enabled=False, exchange="XNYS",
        allow_regular_session=True, allow_post_close=True,
        post_close_until=time(18, 0), allow_premarket=False,
    )
    gate = _make_gate(cfg=cfg, now_ny=datetime(2026, 5, 21, 3, 0, tzinfo=NY_TZ))
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is True


def test_market_hours_stale_allocation_date_denies():
    """Lock 6b-L7."""
    gate = _make_gate(now_ny=datetime(2026, 5, 22, 14, 0, tzinfo=NY_TZ))
    r = gate.check_pre_trade(order_request=_make_request(allocation_date=date(2026, 5, 21)))
    assert r.approved is False
    assert r.reason == "stale_allocation_date"
    assert r.context["allocation_date"] == "2026-05-21"
    assert r.context["today_session"] == "2026-05-22"


def test_market_hours_weekend_session_denies():
    # 2026-05-23 is a Saturday — not a session day.
    gate = _make_gate(now_ny=datetime(2026, 5, 23, 14, 0, tzinfo=NY_TZ))
    r = gate.check_pre_trade(order_request=_make_request(allocation_date=date(2026, 5, 23)))
    assert r.approved is False
    # today_ny_trading_date rolls back to Friday → stale_allocation_date.
    # Either reason is acceptable as long as the order is denied for a
    # non-session-day reason.
    assert r.reason in ("stale_allocation_date", "not_a_session_day")


# === Boundary tests (operational tests #17-#20) ===

def _at(hour, minute, second=0):
    """NY-time datetime at the given clock face on 2026-05-21 (Thu)."""
    return datetime(2026, 5, 21, hour, minute, second, tzinfo=NY_TZ)


def test_boundary_premarket_close_edge():
    """Op-test #17: premarket disabled, 09:29:59 deny, 09:30:00 approve."""
    gate = _make_gate(now_ny=_at(9, 29, 59))
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert r.reason == "outside_placement_window"

    gate2 = _make_gate(now_ny=_at(9, 30, 0))
    r2 = gate2.check_pre_trade(order_request=_make_request())
    assert r2.approved is True


def test_boundary_regular_close_edge():
    """Op-test #18: 16:00:00 approve (regular inclusive right); 16:00:01
    approve via post-close (open-left)."""
    gate = _make_gate(now_ny=_at(16, 0, 0))
    assert gate.check_pre_trade(order_request=_make_request()).approved is True

    gate2 = _make_gate(now_ny=_at(16, 0, 1))
    assert gate2.check_pre_trade(order_request=_make_request()).approved is True


def test_boundary_post_close_cutoff_edge():
    """Op-test #19: post_close_until=18:00 — 18:00:00 approve (inclusive
    right); 18:00:01 deny."""
    gate = _make_gate(now_ny=_at(18, 0, 0))
    assert gate.check_pre_trade(order_request=_make_request()).approved is True

    gate2 = _make_gate(now_ny=_at(18, 0, 1))
    r = gate2.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert r.reason == "outside_placement_window"


def test_boundary_all_disabled_denies_everywhere():
    """Op-test #20: all three window flags false → no valid placement window."""
    from marketpulse.trading.risk_gates.config_provider import MarketHoursConfig
    cfg = MarketHoursConfig(
        enabled=True, exchange="XNYS",
        allow_regular_session=False, allow_post_close=False,
        post_close_until=time(18, 0), allow_premarket=False,
    )
    for hh in (5, 10, 14, 17, 23):
        gate = _make_gate(cfg=cfg, now_ny=_at(hh, 0, 0))
        r = gate.check_pre_trade(order_request=_make_request())
        assert r.approved is False, f"{hh:02d}:00 should deny"
        assert r.reason == "outside_placement_window"


def test_market_hours_17_30_default_passes():
    """Op-test #14: Phase 6a default tick fires at 17:30 NY → must pass."""
    gate = _make_gate(now_ny=_at(17, 30, 0))
    assert gate.check_pre_trade(order_request=_make_request()).approved is True
