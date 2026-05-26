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
        # Phase 6 forward share-sizing: quantity is now derived from
        # position_size / event_price at the OrderRequest boundary.
        # Set position_size so the conversion yields the legacy quantity=10.
        quantity=10,           # legacy field; ignored by _make_order_request
        position_size=1500.0,  # = 10 shares * $150
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
    # T8 / lock 6b+L1: daily_cycle.run no longer takes a price_provider
    # kwarg — the engine owns the provider and uses it at exit time.
    # Keep the local `price_provider` reference for the engine ctor; do
    # NOT thread it into the returned deps bundle.
    _ = price_provider
    return {
        "clock": clock, "engine": engine, "repository": repo,
        "bid_aggregator": aggregator, "allocator": allocator,
        "calendar": calendar, "kill_switch": ks,
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


def test_daily_cycle_run_rejects_price_provider_kwarg(session):
    """T8: price_provider kwarg removed from daily_cycle.run (breaking change)."""
    from marketpulse.trading import daily_cycle
    from marketpulse.trading.price_provider import StubPriceProvider

    deps = _make_deps(
        session,
        fake_now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC),
        allocator=_stub_allocator(expected_winners=[]),
    )
    # Adding price_provider should raise TypeError
    with pytest.raises(TypeError, match="price_provider|unexpected"):
        daily_cycle.run(**deps, price_provider=StubPriceProvider(map={}))


def test_daily_cycle_forward_mode_paper_order_horizon_price_is_null(session):
    """Lock 6b+L1: forward mode never writes horizon_price to paper_order."""
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
    assert orders[0].horizon_price is None


def test_daily_cycle_tick_completed_includes_price_unavailable_count(session):
    """T8 / Lock 6b+L11: TICK_COMPLETED.context surfaces
    engine.last_price_unavailable_count(). With a no-op tick (no OPEN
    positions to exit), the count is 0 — but the key must always be
    present for forward-replay analytics."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading import daily_cycle

    deps = _make_deps(
        session,
        fake_now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC),
        allocator=_stub_allocator(expected_winners=[]),
    )
    daily_cycle.run(**deps)
    tc_audits = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "TICK_COMPLETED"),
    ).scalars().all()
    assert len(tc_audits) == 1
    # Context has the new key (value 0 since no OPEN positions to exit).
    assert "price_unavailable_count" in tc_audits[0].context
    assert tc_audits[0].context["price_unavailable_count"] == 0


# === Phase 6 forward share-sizing — _make_order_request quantization ===
# Added 2026-05-26. PR #117: daily_cycle._make_order_request now converts
# winner.position_size (USD) into integer shares at the OrderRequest
# boundary. Was previously trusting winner.quantity which Phase 5 leaves at
# 0 — see allocation.py:547. Production paper_trading_tick crashed with
# CHECK constraint when bids finally flowed.

def _winner_with_size(*, event_price: float, position_size: float):
    """Bare AllocationWinner with the two fields _make_order_request reads."""
    from marketpulse.backtest.allocation import AllocationWinner
    return AllocationWinner(
        strategy="general", ticker="AAPL",
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        event_price=event_price,
        horizon_date=date(2026, 5, 28),
        horizon_price=None,
        quantity=0,                # legacy field; conversion ignores it
        position_size=position_size,
        weight=1.0, raw_bid_weight=1.0, pool_corr=None,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
        strategy_version="v1",
    )


def test_make_order_request_dollar_to_shares_basic():
    """$1000 / $25 = 40 shares."""
    from marketpulse.trading.daily_cycle import _make_order_request
    from marketpulse.trading.types import AllocationRunId
    req, skip = _make_order_request(
        winner=_winner_with_size(event_price=25.0, position_size=1000.0),
        allocation_run_id=AllocationRunId("test"),
        allocation_date=date(2026, 5, 26),
    )
    assert skip is None
    assert req is not None
    assert req.quantity == 40


def test_make_order_request_dollar_to_shares_expensive():
    """$1000 / $333 = 3 shares (floor)."""
    from marketpulse.trading.daily_cycle import _make_order_request
    from marketpulse.trading.types import AllocationRunId
    req, skip = _make_order_request(
        winner=_winner_with_size(event_price=333.0, position_size=1000.0),
        allocation_run_id=AllocationRunId("test"),
        allocation_date=date(2026, 5, 26),
    )
    assert skip is None
    assert req.quantity == 3


def test_make_order_request_below_share_cost_rejects():
    """$100 / $150 = 0 → reject below_share_cost."""
    from marketpulse.trading.daily_cycle import _make_order_request
    from marketpulse.trading.types import AllocationRunId
    req, skip = _make_order_request(
        winner=_winner_with_size(event_price=150.0, position_size=100.0),
        allocation_run_id=AllocationRunId("test"),
        allocation_date=date(2026, 5, 26),
    )
    assert req is None
    assert skip == "below_share_cost"


def test_make_order_request_zero_price_rejects():
    """event_price=0 → reject invalid_event_price (avoid ZeroDivisionError)."""
    from marketpulse.trading.daily_cycle import _make_order_request
    from marketpulse.trading.types import AllocationRunId
    req, skip = _make_order_request(
        winner=_winner_with_size(event_price=0.0, position_size=1000.0),
        allocation_run_id=AllocationRunId("test"),
        allocation_date=date(2026, 5, 26),
    )
    assert req is None
    assert skip == "invalid_event_price"


def test_make_order_request_negative_price_rejects():
    """event_price<0 → reject invalid_event_price (defensive)."""
    from marketpulse.trading.daily_cycle import _make_order_request
    from marketpulse.trading.types import AllocationRunId
    req, skip = _make_order_request(
        winner=_winner_with_size(event_price=-1.0, position_size=1000.0),
        allocation_run_id=AllocationRunId("test"),
        allocation_date=date(2026, 5, 26),
    )
    assert req is None
    assert skip == "invalid_event_price"


def test_make_order_request_never_emits_quantity_zero():
    """Invariant: success path must never produce quantity=0 (would violate
    paper_order.ck_qty_positive). Either we return a positive quantity or a
    skip reason — never (request, None) with quantity=0."""
    from marketpulse.trading.daily_cycle import _make_order_request
    from marketpulse.trading.types import AllocationRunId
    for px, sz in [(25, 1000), (1, 1), (1000, 1), (333, 1000)]:
        req, _ = _make_order_request(
            winner=_winner_with_size(event_price=float(px), position_size=float(sz)),
            allocation_run_id=AllocationRunId("test"),
            allocation_date=date(2026, 5, 26),
        )
        if req is not None:
            assert req.quantity >= 1, f"quantity=0 leaked for px={px} sz={sz}"
