# Layer: pure
"""6b-T15: CompositeRiskGate tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal


def _make_request(*, risk_intent=None):
    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent
    return OrderRequest(
        strategy="momentum_breakout", ticker="AAPL", quantity=10,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
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


class _ApproveGate:
    name = "approve_gate"

    def check_pre_trade(self, *, order_request):
        from marketpulse.trading.risk_gate import RiskResult
        return RiskResult(approved=True, gate_name=self.name, reason="")


class _DenyGate:
    def __init__(self, *, name, reason, context=None):
        self.name = name
        self._reason = reason
        self._context = context or {}

    def check_pre_trade(self, *, order_request):
        from marketpulse.trading.risk_gate import RiskResult
        return RiskResult(
            approved=False, gate_name=self.name,
            reason=self._reason, context=dict(self._context),
        )


class _ExplodingGate:
    name = "exploder"

    def check_pre_trade(self, *, order_request):
        raise RuntimeError("boom from exploder")


def _make_composite(gates):
    """Lock 6b-L15: composite now accepts gates: Sequence[RiskGate]
    directly — tests construct via the public constructor, NOT
    `__new__` + private attribute. Fakes inject behavior without
    needing to fake deps."""
    from marketpulse.trading.risk_gates.composite import CompositeRiskGate
    return CompositeRiskGate(gates=tuple(gates))


def test_composite_all_approve():
    """Op-test #0: composite approves when all 4 gates approve."""
    c = _make_composite([_ApproveGate(), _ApproveGate(), _ApproveGate(), _ApproveGate()])
    r = c.check_pre_trade(order_request=_make_request())
    assert r.approved is True
    assert r.gate_name == "composite"
    assert r.failed_gates == ()


def test_composite_close_bypass_short_circuits():
    """Lock 6b-L1: CLOSE bypasses the composite — no child gate runs."""
    from marketpulse.trading.types import RiskIntent
    exploder = _ExplodingGate()
    c = _make_composite([exploder, exploder])
    r = c.check_pre_trade(
        order_request=_make_request(risk_intent=RiskIntent.CLOSE),
    )
    assert r.approved is True


def test_composite_flip_denies_unsupported():
    from marketpulse.trading.types import RiskIntent
    c = _make_composite([_ApproveGate(), _ApproveGate()])
    r = c.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.FLIP))
    assert r.approved is False
    assert r.reason == "unsupported_risk_intent"
    assert r.gate_name == "composite"


def test_composite_one_deny_denies_with_failed_gates_list():
    c = _make_composite([
        _ApproveGate(),
        _DenyGate(name="daily_loss", reason="daily_loss_limit_exceeded"),
        _ApproveGate(),
        _ApproveGate(),
    ])
    r = c.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert r.failed_gates == ("daily_loss",)
    assert r.reason == "daily_loss_limit_exceeded"
    assert len(r.context["per_gate"]) == 4


def test_composite_multi_deny_lists_all_failed():
    """Op-test #4: run-all not fail-fast. Even when one gate denies, the
    remaining gates still run and per_gate lists ALL results."""
    c = _make_composite([
        _DenyGate(name="market_hours", reason="outside_placement_window"),
        _DenyGate(name="daily_loss", reason="daily_loss_limit_exceeded"),
        _ApproveGate(),
        _DenyGate(name="sector_exposure", reason="sector_cap_exceeded"),
    ])
    r = c.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert set(r.failed_gates) == {"market_hours", "daily_loss", "sector_exposure"}
    assert "outside_placement_window" in r.reason
    assert "daily_loss_limit_exceeded" in r.reason
    assert len(r.context["per_gate"]) == 4


def test_composite_exception_becomes_per_gate_deny():
    """Op-test #1: gate raise → composite captures + denies + records
    error_type + error in per_gate context."""
    c = _make_composite([
        _ApproveGate(),
        _ExplodingGate(),
        _ApproveGate(),
    ])
    r = c.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert "exploder" in r.failed_gates
    # Per-gate context for the exploder should carry the error type.
    exploder_row = next(g for g in r.context["per_gate"] if g["gate_name"] == "exploder")
    assert exploder_row["context"]["error_type"] == "RuntimeError"
    assert exploder_row["context"]["error"] == "boom from exploder"


def test_composite_normalizes_decimal_in_context_to_str():
    """Lock 6b-L10: Decimals in nested context must serialize as strings.
    The composite normalizes per_gate[*].context recursively so the
    forward_engine audit writer (which json.dumps the context) doesn't
    crash or emit non-deterministic floats."""
    c = _make_composite([
        _DenyGate(
            name="daily_loss",
            reason="x",
            context={
                "today_realized_pnl": Decimal("-500.123456"),
                "nested": {"inner_decimal": Decimal("1.5")},
            },
        ),
    ])
    r = c.check_pre_trade(order_request=_make_request())
    row = r.context["per_gate"][0]
    assert row["context"]["today_realized_pnl"] == "-500.123456"
    assert row["context"]["nested"]["inner_decimal"] == "1.5"
    # Ensure the composite top-level context survives json.dumps round-trip
    # through the canonical audit normalizer (lock 6b-L16 wraps r.context in
    # MappingProxyType; the audit-write boundary normalizes before dumping).
    import json

    from marketpulse.trading.audit_json import normalize_for_json
    json.dumps(normalize_for_json(r.context))
