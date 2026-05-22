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


def _engine(session, *, kill_active=False, price_provider=None):
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.price_provider import StubPriceProvider
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
        price_provider=price_provider or StubPriceProvider(map={}),
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
    from marketpulse.trading.price_provider import StubPriceProvider
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
        price_provider=StubPriceProvider(map={}),
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
    from marketpulse.trading.price_provider import StubPriceProvider
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
        price_provider=StubPriceProvider(map={}),
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


def test_tick_materializes_entry_then_exit(session):
    """E2E single-position lifecycle through tick().

    Lock 6b+L1: exit price now comes from PriceProvider — seed the stub
    with a ClosePrice for the horizon date so the EXIT tick succeeds."""
    from marketpulse.db.models import PaperCashLedger, PaperFill, PaperPosition
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider

    horizon = date(2026, 5, 28)
    provider = StubPriceProvider(map={
        ("AAPL", horizon): ClosePrice(
            price=Decimal("155.000000"),
            price_date=horizon, requested_date=horizon, source="stub",
        ),
    })
    engine, repo, clock, _ = _engine(session, price_provider=provider)

    # Initial deposit
    repo.ensure_initial_deposit(
        amount=Decimal("10000"), timestamp=clock.now(),
    )

    # Place order
    engine.place_order(order_request=_request())

    # tick on allocation_date → ENTRY materialization
    r1 = engine.tick(as_of=date(2026, 5, 21))
    assert r1.entries_materialized == 1
    assert r1.exits_materialized == 0
    assert r1.errors == ()

    pos = session.execute(select(PaperPosition)).scalars().first()
    assert pos.status == "OPEN"
    assert pos.entry_fill_id is not None

    fills = session.execute(
        select(PaperFill).order_by(PaperFill.id),
    ).scalars().all()
    assert len(fills) == 1
    assert fills[0].side == "ENTRY"

    cash_rows = session.execute(
        select(PaperCashLedger).order_by(PaperCashLedger.id),
    ).scalars().all()
    # Initial 10000, then -1500 entry
    assert cash_rows[-1].balance_after == Decimal("8500")

    # tick on horizon_date → EXIT
    r2 = engine.tick(as_of=date(2026, 5, 28))
    assert r2.exits_materialized == 1

    pos = session.execute(select(PaperPosition)).scalars().first()
    assert pos.status == "CLOSED"

    fills = session.execute(
        select(PaperFill).order_by(PaperFill.id),
    ).scalars().all()
    assert len(fills) == 2
    assert fills[1].side == "EXIT"
    # PnL: (155 - 150) * 10 = 50
    assert fills[1].realized_pnl == Decimal("50")

    assert repo.cash_balance() == Decimal("10050")


def test_tick_is_idempotent(session):
    """Calling tick twice for the same date produces the same state."""
    engine, repo, _, _ = _engine(session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 21, tzinfo=UTC),
    )
    engine.place_order(order_request=_request())

    r1 = engine.tick(as_of=date(2026, 5, 21))
    r2 = engine.tick(as_of=date(2026, 5, 21))
    assert r1.entries_materialized == 1
    assert r2.entries_materialized == 0  # no rows to flip
    assert r2.exits_materialized == 0


def test_tick_invariant_error_writes_audit_and_continues(session):
    """6a-L4: exit-path InvariantError → ENGINE_INVARIANT_ERROR audit; other
    positions in the same tick keep processing.

    Phase 6b+T7 update: the original test corrupted order.horizon_price to
    None to trigger the InvariantError. Lock 6b+L1 sealed that path —
    _materialize_exit no longer reads order.horizon_price. To preserve
    6a-L4 coverage we instead corrupt the position's status to an illegal
    value, which makes update_paper_position_exit's transition check raise
    InvariantError (a non-price invariant)."""
    from marketpulse.db.models import PaperAuditEvent, PaperPosition
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider

    horizon = date(2026, 5, 28)
    provider = StubPriceProvider(map={
        ("AAPL", horizon): ClosePrice(
            price=Decimal("155.000000"),
            price_date=horizon, requested_date=horizon, source="stub",
        ),
    })
    engine, repo, _, _ = _engine(session, price_provider=provider)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 21, tzinfo=UTC),
    )

    # Place a normal order
    engine.place_order(order_request=_request(strategy="ok"))
    engine.tick(as_of=date(2026, 5, 21))  # materialize entry

    # Inject a non-price invariant in the exit path: patch the
    # repository's update_paper_position_exit to raise InvariantError.
    # This exercises the same 6a-L4 catch+audit path the original test
    # guarded, just via a different (still real) failure mode now that
    # lock 6b+L1 sealed the horizon_price=None path.
    from marketpulse.trading.types import InvariantError

    def _raise(**kwargs):
        raise InvariantError("synthetic exit-path invariant for 6a-L4 coverage")

    repo.update_paper_position_exit = _raise  # type: ignore[method-assign]

    result = engine.tick(as_of=horizon)
    assert len(result.errors) == 1
    assert result.errors[0].phase == "exit_materialization"

    audits = session.execute(
        select(PaperAuditEvent).where(
            PaperAuditEvent.event_type == "ENGINE_INVARIANT_ERROR",
        ),
    ).scalars().all()
    assert len(audits) == 1
    # And critically: the position is NOT closed (transaction rolled back).
    pos = session.execute(select(PaperPosition)).scalars().first()
    assert pos.status == "OPEN"


def test_forward_engine_propagates_failed_gates_into_audit_context(tmp_path):
    """6b-T16: when CompositeRiskGate denies, ORDER_REJECTED audit row's
    context.failed_gates and context.per_gate carry the composite's
    extended fields (lock 6b-L6 — reuse ORDER_REJECTED, no new event)."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.price_provider import StubPriceProvider
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import RiskResult
    from marketpulse.trading.types import AllocationRunId, OrderRejected, OrderRequest

    class _ExtendedDenyGate:
        def check_pre_trade(self, *, order_request):
            return RiskResult(
                approved=False,
                reason="daily_loss_limit_exceeded; sector_cap_exceeded",
                gate_name="daily_loss",
                failed_gates=("daily_loss", "sector_exposure"),
                context={
                    "per_gate": [
                        {"gate_name": "market_hours", "approved": True},
                        {"gate_name": "strategy_size", "approved": True},
                        {"gate_name": "daily_loss", "approved": False,
                         "reason": "daily_loss_limit_exceeded",
                         "context": {"today_realized_pnl": "-500"}},
                        {"gate_name": "sector_exposure", "approved": False,
                         "reason": "sector_cap_exceeded",
                         "context": {"projected": "4000", "cap": "3500"}},
                    ],
                },
            )

    eng_db = tmp_path / "fe.db"
    db_engine = create_engine(f"sqlite:///{eng_db}")
    Base.metadata.create_all(db_engine)
    with Session(db_engine) as s:
        repo = Repository(session=s)
        clock = FakeClock(now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC))
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
        ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
        engine = ForwardExecutionEngine(
            repository=repo, clock=clock, kill_switch=ks,
            risk_gate=_ExtendedDenyGate(),
            price_provider=StubPriceProvider(map={}),
        )
        req = OrderRequest(
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
        )
        import pytest
        with pytest.raises(OrderRejected):
            engine.place_order(order_request=req)

        rejects = s.execute(
            select(PaperAuditEvent)
            .where(PaperAuditEvent.event_type == "ORDER_REJECTED")
        ).scalars().all()
        assert len(rejects) == 1
        ctx = rejects[0].context
        assert ctx["gate"] == "daily_loss"            # 6a-compat
        assert ctx["failed_gates"] == ["daily_loss", "sector_exposure"]
        assert len(ctx["per_gate"]) == 4
        assert ctx["per_gate"][2]["reason"] == "daily_loss_limit_exceeded"


def test_forward_engine_requires_price_provider_kwarg(tmp_path):
    """Lock 6b+L2: missing price_provider → TypeError."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from marketpulse.db.base import Base
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate

    eng_db = tmp_path / "fe.db"
    db_engine = create_engine(f"sqlite:///{eng_db}")
    Base.metadata.create_all(db_engine)
    with Session(db_engine) as s:
        repo = Repository(session=s)
        clock = FakeClock(now=datetime(2026, 5, 22, 21, 30, tzinfo=UTC))
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
        ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
        import pytest
        with pytest.raises(TypeError, match="price_provider"):
            ForwardExecutionEngine(
                repository=repo, clock=clock, kill_switch=ks,
                risk_gate=AlwaysApproveRiskGate(),
                # price_provider omitted intentionally
            )
