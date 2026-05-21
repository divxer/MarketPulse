# Layer: behavioral
"""6a-2.5+: ForwardExecutionEngine.place_order + cancel_order + tick."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'fe.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _engine(session, *, kill_active=False):
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate

    repo = Repository(session=session)
    clock = FakeClock(now=datetime(2026, 5, 21, 17, 30, tzinfo=UTC))
    ks = KillSwitchState(env_var="MP_NEVER_SET_KS", repository=repo)
    if kill_active:
        ks.flip(
            new_state=True, reason="test", actor="test",
            timestamp=clock.now(),
        )
    engine = ForwardExecutionEngine(
        repository=repo, clock=clock,
        kill_switch=ks, risk_gate=AlwaysApproveRiskGate(),
    )
    return engine, repo, clock, ks


def _request(strategy="s", run_id="paper-2026-05-21"):
    from marketpulse.trading.types import AllocationRunId, OrderRequest
    return OrderRequest(
        strategy=strategy, ticker="AAPL", quantity=10,
        event_time=datetime(2026, 5, 21, 14, 30, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150"), horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155"),
        allocation_run_id=AllocationRunId(run_id),
        strategy_version="v0", allocator_version="v0",
        execution_engine_version="v0",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60, rewarded_for_negative_corr=False,
        would_change_rank=False, size_clamped_by_override=False,
    )


def test_place_order_accepted_writes_order_and_audit(session):
    """6a-L2: PlaceOrderResult(created=True, duplicate=False) on first call."""
    from marketpulse.db.models import PaperAuditEvent, PaperOrder

    engine, _, _, _ = _engine(session)
    result = engine.place_order(order_request=_request())
    assert result.created is True
    assert result.duplicate is False

    orders = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders) == 1

    audits = session.execute(select(PaperAuditEvent)).scalars().all()
    assert any(a.event_type == "ORDER_PLACED" for a in audits)


def test_place_order_idempotency_hit_returns_duplicate(session):
    """6a-L2: second call with same key returns (created=False, duplicate=True)."""
    engine, _, _, _ = _engine(session)
    r1 = engine.place_order(order_request=_request())
    r2 = engine.place_order(order_request=_request())
    assert r1.order_id == r2.order_id
    assert r2.created is False
    assert r2.duplicate is True


def test_place_order_idempotency_writes_duplicate_audit_once(session):
    """6a-L5: ORDER_PLACED_DUPLICATE deduped per (key, tick_date).
    Three replays produce 1 audit row total."""
    from marketpulse.db.models import PaperAuditEvent

    engine, _, _, _ = _engine(session)
    engine.place_order(order_request=_request())
    engine.place_order(order_request=_request())
    engine.place_order(order_request=_request())

    dup_audits = session.execute(
        select(PaperAuditEvent).where(
            PaperAuditEvent.event_type == "ORDER_PLACED_DUPLICATE",
        ),
    ).scalars().all()
    assert len(dup_audits) == 1


def test_place_order_kill_switch_active_rejects(session):
    """Kill switch active → ORDER_REJECTED audit + OrderRejected raised."""
    from marketpulse.db.models import PaperAuditEvent, PaperOrder
    from marketpulse.trading.types import OrderRejected

    engine, _, _, _ = _engine(session, kill_active=True)
    with pytest.raises(OrderRejected, match="kill_switch_active"):
        engine.place_order(order_request=_request())

    orders = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders) == 0

    rejects = session.execute(
        select(PaperAuditEvent).where(
            PaperAuditEvent.event_type == "ORDER_REJECTED",
        ),
    ).scalars().all()
    assert len(rejects) == 1
    assert rejects[0].reason == "kill_switch_active"


def test_place_order_risk_gate_exception_fail_closed(session):
    """6a-L3: arbitrary risk_gate exception → ORDER_REJECTED("risk_gate_error")."""
    from marketpulse.db.models import PaperAuditEvent, PaperOrder
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.types import OrderRejected

    class BoomGate:
        def check_pre_trade(self, *, order_request):
            raise RuntimeError("boom")

    repo = Repository(session=session)
    clock = FakeClock(now=datetime(2026, 5, 21, 17, 30, tzinfo=UTC))
    engine = ForwardExecutionEngine(
        repository=repo, clock=clock,
        kill_switch=KillSwitchState(env_var="MP_NEVER", repository=repo),
        risk_gate=BoomGate(),
    )

    with pytest.raises(OrderRejected, match="risk_gate_error"):
        engine.place_order(order_request=_request())

    orders = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders) == 0

    rejects = session.execute(
        select(PaperAuditEvent).where(
            PaperAuditEvent.event_type == "ORDER_REJECTED",
        ),
    ).scalars().all()
    assert len(rejects) == 1
    assert rejects[0].reason == "risk_gate_error"
    assert rejects[0].context["error_type"] == "RuntimeError"


def test_rejection_audit_committed_before_exception(session, monkeypatch):
    """Lock ix / 6a-L3: ORDER_REJECTED audit row MUST be committed (visible
    in a fresh session) before OrderRejected is raised."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import RiskResult
    from marketpulse.trading.types import OrderRejected

    captured_audit_ids: list[int] = []

    class _RejectGate:
        def check_pre_trade(self, *, order_request):
            return RiskResult(
                approved=False, reason="test_block", gate_name="g",
            )

    repo = Repository(session=session)
    # Spy on repo.write_audit_event — capture id immediately after flush.
    real_write = repo.write_audit_event

    def spy(*args, **kwargs):
        row = real_write(*args, **kwargs)
        captured_audit_ids.append(row.id)
        return row

    monkeypatch.setattr(repo, "write_audit_event", spy)

    engine = ForwardExecutionEngine(
        repository=repo,
        clock=FakeClock(now=datetime(2026, 5, 21, 17, 30, tzinfo=UTC)),
        kill_switch=KillSwitchState(env_var="MP_NEVER", repository=repo),
        risk_gate=_RejectGate(),
    )

    with pytest.raises(OrderRejected, match="test_block"):
        engine.place_order(order_request=_request())

    # Same-session check: the audit row must be visible after the raise.
    assert len(captured_audit_ids) == 1

    # Fresh-session check: the audit row must have been committed
    # (would be invisible to a new session if still in-flight).
    fresh_eng = session.bind
    with Session(fresh_eng) as fresh:
        rows = fresh.execute(
            select(PaperAuditEvent).where(
                PaperAuditEvent.id == captured_audit_ids[0],
            ),
        ).scalars().all()
        assert len(rows) == 1, (
            "ORDER_REJECTED audit must commit BEFORE OrderRejected is "
            "raised (lock ix). A rejection without a committed audit is "
            "not a valid completed rejection."
        )


def test_cancel_order_flips_placed_to_cancelled(session):
    from marketpulse.db.models import PaperAuditEvent

    engine, repo, _, _ = _engine(session)
    result = engine.place_order(order_request=_request())
    engine.cancel_order(order_id=result.order_id)

    order = repo.find_paper_order_by_id(int(result.order_id))
    assert order.status == "CANCELLED"

    audits = session.execute(
        select(PaperAuditEvent).where(
            PaperAuditEvent.event_type == "ORDER_CANCELLED",
        ),
    ).scalars().all()
    assert len(audits) == 1


def test_cancel_order_idempotent_on_already_cancelled(session):
    engine, repo, _, _ = _engine(session)
    result = engine.place_order(order_request=_request())
    engine.cancel_order(order_id=result.order_id)
    # Second call: no-op
    engine.cancel_order(order_id=result.order_id)
    order = repo.find_paper_order_by_id(int(result.order_id))
    assert order.status == "CANCELLED"
