# Layer: stateful
"""6b+T7b: ForwardExecutionEngine._materialize_exit price provider integration.

Covers locks 6b+L1, L4, L7, L8, L11."""

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


def _make_engine(session, *, price_provider, now=None):
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate
    repo = Repository(session=session)
    clock = FakeClock(now=now or datetime(2026, 5, 22, 21, 30, tzinfo=UTC))
    repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
    ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
    return ForwardExecutionEngine(
        repository=repo, clock=clock, kill_switch=ks,
        risk_gate=AlwaysApproveRiskGate(),
        price_provider=price_provider,
    ), repo, clock


def _place_and_open(repo, clock, *, ticker="AAPL", horizon_date=date(2026, 5, 22)):
    """Helper: create a paper_order + paper_position in OPEN state with the
    given horizon_date so we can directly test _materialize_exit.

    Inserts a real ENTRY fill row so the CHECK constraint
    `ck_paper_position_closed_both_set` (entry_fill_id NOT NULL when
    CLOSED) is satisfied when the engine later closes this position."""
    from marketpulse.db.models import PaperFill, PaperOrder, PaperPosition

    order = PaperOrder(
        idempotency_key="k1", allocation_run_id="r1",
        strategy="momentum_breakout", ticker=ticker, quantity=10,
        event_time=datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 22),
        horizon_date=horizon_date,
        placed_at=datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
        filled_at=datetime(2026, 5, 22, 14, 1, tzinfo=UTC),
        event_price=Decimal("150.000000"),
        horizon_price=None,    # forward mode: NULL
        status="ENTRY_FILLED",
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
    repo._session.add(order)
    repo._session.flush()
    position = PaperPosition(
        order_id=order.id, strategy=order.strategy, ticker=order.ticker,
        quantity=order.quantity,
        entry_price=Decimal("150.000000"),
        entry_date=date(2026, 5, 22),
        horizon_date=horizon_date,
        status="OPEN",
        opened_at=datetime(2026, 5, 22, 14, 1, tzinfo=UTC),
        entry_fill_id=None, exit_fill_id=None,
    )
    repo._session.add(position)
    repo._session.flush()
    entry_fill = PaperFill(
        order_id=order.id, position_id=position.id, side="ENTRY",
        price=Decimal("150.000000"), quantity=order.quantity,
        filled_at=datetime(2026, 5, 22, 14, 1, tzinfo=UTC),
        cash_delta=Decimal("-1500.000000"),
        realized_pnl=None,
    )
    repo._session.add(entry_fill)
    repo._session.flush()
    position.entry_fill_id = entry_fill.id
    repo._session.flush()
    return order, position


def test_materialize_exit_returns_true_on_close_price_success(session):
    """Happy path: provider returns ClosePrice -> True; position CLOSED."""
    from marketpulse.db.models import PaperPosition
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider

    horizon = date(2026, 5, 22)
    close = ClosePrice(
        price=Decimal("155.250000"),
        price_date=horizon,
        requested_date=horizon,
        source="stub",
    )
    provider = StubPriceProvider(map={("AAPL", horizon): close})
    engine, repo, clock = _make_engine(session, price_provider=provider)

    _, position = _place_and_open(repo, clock, horizon_date=horizon)

    result = engine._materialize_exit(position, exit_date=date(2026, 5, 22))

    assert result is True
    refreshed = session.execute(
        select(PaperPosition).where(PaperPosition.id == position.id)
    ).scalar_one()
    assert refreshed.status == "CLOSED"
    assert refreshed.exit_price == Decimal("155.250000")
    assert refreshed.realized_pnl == Decimal("52.500000")   # (155.25 - 150) * 10


def test_materialize_exit_returns_false_on_price_unavailable(session):
    """Lock 6b+L7: provider returns None -> False; position stays OPEN."""
    from marketpulse.db.models import PaperPosition
    from marketpulse.trading.price_provider import StubPriceProvider

    provider = StubPriceProvider(map={})    # empty - every call returns None
    engine, repo, clock = _make_engine(session, price_provider=provider)
    _, position = _place_and_open(repo, clock, horizon_date=date(2026, 5, 22))

    result = engine._materialize_exit(position, exit_date=date(2026, 5, 22))

    assert result is False
    refreshed = session.execute(
        select(PaperPosition).where(PaperPosition.id == position.id)
    ).scalar_one()
    assert refreshed.status == "OPEN"
    assert refreshed.exit_price is None
    assert refreshed.realized_pnl is None


def test_price_unavailable_writes_audit_with_provider_provenance(session):
    """Locks 6b+L4 (order_id=position.order_id), 6b+L8 (provider source/
    lookback in audit)."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.price_provider import StubPriceProvider

    provider = StubPriceProvider(map={})    # always None
    engine, repo, clock = _make_engine(session, price_provider=provider)
    order, position = _place_and_open(repo, clock, horizon_date=date(2026, 5, 22))

    engine._materialize_exit(position, exit_date=date(2026, 5, 23))

    audits = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "PRICE_UNAVAILABLE")
    ).scalars().all()
    assert len(audits) == 1
    a = audits[0]
    assert a.order_id == order.id    # lock 6b+L4
    assert a.context["position_id"] == position.id
    assert a.context["ticker"] == "AAPL"
    assert a.context["horizon_date"] == "2026-05-22"
    assert a.context["as_of"] == "2026-05-23"
    assert a.context["source"] == "stub"        # lock 6b+L8 - from provider
    assert a.context["lookback_days"] == 0      # lock 6b+L8 - from provider
    assert a.context["attempt_count"] == 1


def test_attempt_count_progression_1_2_3(session):
    """Lock 6b+L9: 3 consecutive PRICE_UNAVAILABLE writes 1, 2, 3."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.price_provider import StubPriceProvider

    provider = StubPriceProvider(map={})
    engine, repo, clock = _make_engine(session, price_provider=provider)
    _, position = _place_and_open(repo, clock, horizon_date=date(2026, 5, 22))

    for _ in range(3):
        engine._materialize_exit(position, exit_date=date(2026, 5, 23))

    audits = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "PRICE_UNAVAILABLE")
        .order_by(PaperAuditEvent.id)
    ).scalars().all()
    assert [a.context["attempt_count"] for a in audits] == [1, 2, 3]


def test_position_closed_audit_has_provenance_fields(session):
    """4 new fields: requested_horizon_date, actual_price_date, price_source,
    roll_policy."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider

    requested = date(2026, 5, 26)    # Memorial Day (US 2026)
    actual = date(2026, 5, 22)        # Friday before
    close = ClosePrice(
        price=Decimal("155.250000"),
        price_date=actual,
        requested_date=requested,
        source="stub",
    )
    provider = StubPriceProvider(map={("AAPL", requested): close})
    engine, repo, clock = _make_engine(session, price_provider=provider)
    _, position = _place_and_open(repo, clock, horizon_date=requested)

    engine._materialize_exit(position, exit_date=date(2026, 5, 27))

    audits = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "POSITION_CLOSED")
    ).scalars().all()
    assert len(audits) == 1
    ctx = audits[0].context
    assert ctx["requested_horizon_date"] == "2026-05-26"
    assert ctx["actual_price_date"] == "2026-05-22"
    assert ctx["price_source"] == "stub"
    assert ctx["roll_policy"] == "previous_available_close"


def test_position_closed_audit_roll_policy_exact_match(session):
    """When price_date == requested_date -> roll_policy=exact_match."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider

    horizon = date(2026, 5, 22)
    close = ClosePrice(
        price=Decimal("155.250000"),
        price_date=horizon, requested_date=horizon, source="stub",
    )
    provider = StubPriceProvider(map={("AAPL", horizon): close})
    engine, repo, clock = _make_engine(session, price_provider=provider)
    _, position = _place_and_open(repo, clock, horizon_date=horizon)

    engine._materialize_exit(position, exit_date=horizon)

    audit = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "POSITION_CLOSED")
    ).scalar_one()
    assert audit.context["roll_policy"] == "exact_match"


def test_tick_returns_no_errors_when_only_price_unavailable(session):
    """Lock 6b+L7: PRICE_UNAVAILABLE does NOT populate TickResult.errors."""
    from marketpulse.trading.price_provider import StubPriceProvider

    provider = StubPriceProvider(map={})
    engine, repo, clock = _make_engine(session, price_provider=provider)
    _, _ = _place_and_open(repo, clock, horizon_date=date(2026, 5, 22))

    result = engine.tick(as_of=date(2026, 5, 23))

    assert result.errors == ()
    assert result.exits_materialized == 0
    assert engine.last_price_unavailable_count() == 1


def test_last_price_unavailable_count_resets_each_tick(session):
    """Lock 6b+L11: counter resets at start of every tick()."""
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider

    # Tick 1: 1 PRICE_UNAVAILABLE
    provider = StubPriceProvider(map={})
    engine, repo, clock = _make_engine(session, price_provider=provider)
    _, _ = _place_and_open(repo, clock, horizon_date=date(2026, 5, 22))
    engine.tick(as_of=date(2026, 5, 23))
    assert engine.last_price_unavailable_count() == 1

    # Tick 2: same engine, fresh provider with valid price -> 0 unavailable
    engine._price_provider = StubPriceProvider(map={
        ("AAPL", date(2026, 5, 22)): ClosePrice(
            price=Decimal("155.000000"),
            price_date=date(2026, 5, 22),
            requested_date=date(2026, 5, 22),
            source="stub",
        ),
    })
    engine.tick(as_of=date(2026, 5, 23))
    assert engine.last_price_unavailable_count() == 0    # NOT stale 1
