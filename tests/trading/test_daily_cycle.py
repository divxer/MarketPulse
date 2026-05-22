# Layer: stateful
"""6a-3.2: daily_cycle.run orchestration tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from marketpulse.trading.calendar import NYTradingCalendar


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'dc.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _stub_allocator(*, expected_winners: list[Any]):
    """Returns a callable matching the 5-kwarg shape daily_cycle calls."""
    from marketpulse.backtest.allocation import AllocationResult

    def _alloc(*, bids, existing_positions, cash_available,
               allocation_context, sizing_context,
               # Phase 6a fix1: forward-mode kernel context kwargs
               daily_curves=None,
               daily_strategy_contribution_returns=None,
               daily_pool_returns=None,
               sector_provider=None,
               price_provider=None):
        return AllocationResult(
            winners=tuple(expected_winners),
            blocked=(),
            cash_used=0.0,
            cash_remaining=float(cash_available),
            timeline=tuple(expected_winners),
        )
    _alloc.__version__ = "v0"
    return _alloc


def _winner_for(ticker, strategy, allocation_date):
    from marketpulse.backtest.allocation import AllocationWinner
    return AllocationWinner(
        strategy=strategy, ticker=ticker,
        event_time=datetime(
            allocation_date.year, allocation_date.month, allocation_date.day,
            14, 0, tzinfo=UTC,
        ),
        event_price=150.0,
        horizon_date=allocation_date + timedelta(days=7),
        horizon_price=155.0,
        quantity=10,
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
        strategy_version="v0",
    )


def _make_deps(session, *, fake_now, allocator):
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.price_provider import StubPriceProvider
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate

    clock = FakeClock(now=fake_now)
    calendar = NYTradingCalendar()
    repo = Repository(session=session)
    repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
    risk = AlwaysApproveRiskGate()
    ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
    price_provider = StubPriceProvider(map={})
    engine = ForwardExecutionEngine(
        repository=repo, clock=clock, kill_switch=ks, risk_gate=risk,
        price_provider=price_provider,
    )
    aggregator = BidAggregator(session=session, calendar=calendar)
    return {
        "clock": clock, "engine": engine, "repository": repo,
        "bid_aggregator": aggregator, "allocator": allocator,
        "calendar": calendar, "kill_switch": ks,
        "price_provider": price_provider,
    }


def test_daily_cycle_places_orders_and_writes_tick_completed(session):
    from marketpulse.trading import daily_cycle

    deps = _make_deps(
        session,
        fake_now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC),  # 17:30 NY
        allocator=_stub_allocator(expected_winners=[
            _winner_for("AAPL", "momentum", date(2026, 5, 21)),
        ]),
    )
    result = daily_cycle.run(**deps)

    assert result.tick_date == date(2026, 5, 21)
    assert result.allocation_run_id == "paper-2026-05-21"
    assert result.orders_placed == 1
    assert result.cycle_status == "completed"

    # TICK_COMPLETED written
    from marketpulse.db.models import PaperAuditEvent
    audits = session.execute(select(PaperAuditEvent)).scalars().all()
    assert any(a.event_type == "TICK_COMPLETED" for a in audits)


def test_daily_cycle_stamps_real_version_constants(session):
    """Lock xxviii (replay determinism): paper_order.allocator_version
    and execution_engine_version MUST be the actual constants from
    allocation.py / forward_engine.py, NOT hardcoded "v0" stubs.
    Review-fix regression — prevents the daily_cycle from drifting back
    to hardcoded version strings."""
    from marketpulse.backtest.allocation import ALLOCATOR_VERSION
    from marketpulse.db.models import PaperOrder
    from marketpulse.trading import daily_cycle
    from marketpulse.trading.forward_engine import EXECUTION_ENGINE_VERSION

    deps = _make_deps(
        session,
        fake_now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC),
        allocator=_stub_allocator(expected_winners=[
            _winner_for("AAPL", "momentum", date(2026, 5, 21)),
        ]),
    )
    daily_cycle.run(**deps)

    order = session.execute(select(PaperOrder)).scalars().first()
    assert order is not None
    assert order.allocator_version == ALLOCATOR_VERSION, (
        f"expected allocator_version={ALLOCATOR_VERSION!r}, got "
        f"{order.allocator_version!r} — daily_cycle hardcoded back to a stub?"
    )
    assert order.execution_engine_version == EXECUTION_ENGINE_VERSION, (
        f"expected execution_engine_version={EXECUTION_ENGINE_VERSION!r}, got "
        f"{order.execution_engine_version!r}"
    )
    # Sanity: the constants should look like real semver tags, not "v0"
    assert ALLOCATOR_VERSION != "v0"
    assert EXECUTION_ENGINE_VERSION != "v0"


def test_daily_cycle_same_day_rerun_is_no_op(session):
    """6a-L7: same-day rerun → 0 new orders, 0 new TICK_COMPLETED rows."""
    from marketpulse.db.models import PaperAuditEvent, PaperOrder
    from marketpulse.trading import daily_cycle

    fake_now = datetime(2026, 5, 21, 21, 30, tzinfo=UTC)
    alloc = _stub_allocator(expected_winners=[
        _winner_for("AAPL", "momentum", date(2026, 5, 21)),
    ])
    deps = _make_deps(session, fake_now=fake_now, allocator=alloc)
    daily_cycle.run(**deps)

    # Rerun — same clock, same allocator
    deps = _make_deps(session, fake_now=fake_now, allocator=alloc)
    r2 = daily_cycle.run(**deps)
    assert r2.orders_placed == 0
    assert r2.duplicates_skipped == 1

    orders = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders) == 1

    tick_completed = session.execute(
        select(PaperAuditEvent).where(PaperAuditEvent.event_type == "TICK_COMPLETED")
    ).scalars().all()
    assert len(tick_completed) == 1


def test_daily_cycle_kill_switch_cycle_level_skip(session):
    """6a-L8: kill switch active → KILL_SWITCH_CYCLE_SKIPPED audit;
    0 new paper_order rows. tick() still runs."""
    from marketpulse.db.models import PaperAuditEvent, PaperOrder
    from marketpulse.trading import daily_cycle

    fake_now = datetime(2026, 5, 21, 21, 30, tzinfo=UTC)
    deps = _make_deps(
        session, fake_now=fake_now,
        allocator=_stub_allocator(expected_winners=[
            _winner_for("AAPL", "momentum", date(2026, 5, 21)),
        ]),
    )
    deps["kill_switch"].flip(
        new_state=True, reason="test", actor="t",
        timestamp=deps["clock"].now(),
    )

    result = daily_cycle.run(**deps)
    assert result.cycle_status == "kill_switch_skipped"
    assert result.orders_placed == 0

    orders = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders) == 0

    skips = session.execute(
        select(PaperAuditEvent).where(
            PaperAuditEvent.event_type == "KILL_SWITCH_CYCLE_SKIPPED",
        ),
    ).scalars().all()
    assert len(skips) == 1


def test_daily_cycle_allocator_exception_writes_audit_and_still_ticks(session):
    """fix1: when the allocator raises, daily_cycle MUST write an
    ENGINE_INVARIANT_ERROR(phase=allocation) audit, skip place_order,
    but STILL call engine.tick(as_of) so OPEN positions can close at
    horizon. cycle_status = 'completed_with_errors'."""
    from marketpulse.db.models import PaperAuditEvent, PaperOrder
    from marketpulse.trading import daily_cycle

    def _exploding_allocator(**kwargs):
        raise TypeError(
            "allocate_for_day() missing required keyword-only argument: foo",
        )

    deps = _make_deps(
        session,
        fake_now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC),
        allocator=_exploding_allocator,
    )
    result = daily_cycle.run(**deps)

    # 0 orders placed, but cycle still completes (didn't propagate)
    assert result.orders_placed == 0
    assert result.cycle_status == "completed_with_errors"

    # No paper_order rows
    orders = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders) == 0

    # ENGINE_INVARIANT_ERROR audit with phase=allocation present
    errs = session.execute(
        select(PaperAuditEvent).where(
            PaperAuditEvent.event_type == "ENGINE_INVARIANT_ERROR",
        ),
    ).scalars().all()
    assert len(errs) == 1
    assert errs[0].reason == "allocator_failed"
    assert errs[0].context["phase"] == "allocation"
    assert errs[0].context["error_type"] == "TypeError"

    # TICK_COMPLETED still written — tick ran for due positions
    tick_completed = session.execute(
        select(PaperAuditEvent).where(
            PaperAuditEvent.event_type == "TICK_COMPLETED",
        ),
    ).scalars().all()
    assert len(tick_completed) == 1
    assert tick_completed[0].context["status"] == "completed_with_errors"


def test_daily_cycle_forward_writes_horizon_price_null(session):
    """Lock 6b+L1: even at T3 (shim stage), forward path never writes
    a non-None horizon_price into paper_order. T8 strengthens by
    removing the kwarg entirely; this test guards the invariant from T3
    onward."""
    from marketpulse.db.models import PaperOrder
    from marketpulse.trading import daily_cycle

    deps = _make_deps(
        session,
        fake_now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC),
        allocator=_stub_allocator(expected_winners=[
            _winner_for("AAPL", "momentum", date(2026, 5, 21)),
        ]),
    )
    daily_cycle.run(**deps)
    orders = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders) == 1
    assert orders[0].horizon_price is None, (
        "Lock 6b+L1: forward mode must write horizon_price=NULL. If you "
        "see a non-None value here, daily_cycle._make_order_request is "
        "passing winner.horizon_price through instead of forcing None."
    )
