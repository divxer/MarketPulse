# Layer: stateful
"""6a-4.1: Multi-day E2E stateful suite.

Walks one allocation winner from D0 place → D0 entry → D1-D3 idle →
D5 horizon exit, and asserts the cash-ledger invariant Σ delta ==
latest balance_after holds throughout.

Memorial Day rollback (R7-B): the idle loop intentionally skips
2026-05-25 (US market closed) because today_ny_trading_date rolls
non-sessions back to the prior session, which would re-process Fri 22
as a no-op replay rather than represent a new idle day. A dedicated
test exercises that rollback semantics explicitly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'e2e.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _seed_event(session, *, ticker, event_time, strategy="momentum",
                event_price=150.0, strategy_version="v0"):
    """Seed an EvaluationEvent — strategy lives in payload JSON
    (canonical reader: marketpulse/backtest/queries.py; mirrors the
    pattern in tests/trading/test_bid_aggregator.py)."""
    from marketpulse.db.models import EvaluationEvent

    payload: dict = {"source": "stock_analysis"}
    if strategy is not None:
        payload["strategy"] = strategy
    if strategy_version is not None:
        payload["strategy_version"] = strategy_version

    e = EvaluationEvent(
        event_type="ai_analysis",
        subtype="bullish",
        ticker=ticker,
        event_time=event_time,
        event_price=event_price,
        payload=payload,
    )
    session.add(e)
    session.commit()
    return e


def _stub_allocator(*, horizon_date, horizon_price=155.0,
                    event_price=150.0, quantity=10):
    """Returns a stub allocator function matching daily_cycle's call
    shape. Echoes back the first bid as a single AllocationWinner
    with explicit horizon_date / horizon_price so the engine's exit
    leg has everything it needs. Empty bids → empty winners.
    """
    from marketpulse.backtest.allocation import AllocationResult, AllocationWinner

    def _alloc(*, bids, existing_positions, cash_available,
               allocation_context, sizing_context,
               # Phase 6a fix1: forward-mode kernel context kwargs
               daily_curves=None,
               daily_strategy_contribution_returns=None,
               daily_pool_returns=None,
               sector_provider=None,
               price_provider=None):
        if not bids:
            return AllocationResult(
                winners=(), blocked=(),
                cash_used=0.0,
                cash_remaining=float(cash_available),
            )
        b = bids[0]
        winner = AllocationWinner(
            strategy=b.strategy, ticker=b.ticker,
            event_time=b.event_time,
            event_price=event_price,
            horizon_date=horizon_date,
            horizon_price=horizon_price,
            quantity=quantity,
            weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
            contribution_multiplier=1.0, adjusted_bid_weight=1.0,
            effective_corr_window=60,
            rewarded_for_negative_corr=False,
            would_change_rank=False,
            size_clamped_by_override=False,
            strategy_version=b.strategy_version,
        )
        return AllocationResult(
            winners=(winner,), blocked=(),
            cash_used=float(event_price * quantity),
            cash_remaining=float(cash_available)
            - float(event_price * quantity),
            timeline=(winner,),
        )

    _alloc.__version__ = "v0"
    return _alloc


def _make_deps(session, *, clock, allocator, horizon_price_map=None):
    """Build the dependency bundle daily_cycle.run requires. The
    Repository is constructed once per call but is bound to the same
    SQLAlchemy session — ensure_initial_deposit is idempotent, so this
    is safe to call across simulated days."""
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.price_provider import StubPriceProvider
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate

    calendar = NYTradingCalendar()
    repo = Repository(session=session)
    repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
    risk = AlwaysApproveRiskGate()
    ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
    price_provider = StubPriceProvider(
        map=horizon_price_map or {},
    )
    engine = ForwardExecutionEngine(
        repository=repo, clock=clock, kill_switch=ks, risk_gate=risk,
        price_provider=price_provider,
    )
    aggregator = BidAggregator(session=session, calendar=calendar)
    # T8 / lock 6b+L1: daily_cycle.run no longer accepts a price_provider
    # kwarg. The engine owns the provider; keep the local reference only
    # for the ForwardExecutionEngine ctor above.
    _ = price_provider
    return {
        "clock": clock, "engine": engine, "repository": repo,
        "bid_aggregator": aggregator, "allocator": allocator,
        "calendar": calendar, "kill_switch": ks,
    }


def _assert_cash_invariant(session) -> None:
    """Σ paper_cash_ledger.delta == latest paper_cash_ledger.balance_after."""
    from marketpulse.db.models import PaperCashLedger

    rows = session.execute(
        select(PaperCashLedger).order_by(PaperCashLedger.id),
    ).scalars().all()
    assert rows, "expected at least one cash-ledger row"
    total = sum((r.delta for r in rows), start=Decimal("0"))
    assert total == rows[-1].balance_after, (
        f"cash invariant violated: Σ delta={total} "
        f"!= balance_after={rows[-1].balance_after}"
    )


def test_full_5day_lifecycle_place_to_close(session):
    """D0: tick → 1 order placed + ENTRY_FILLED + position OPEN.
       D1-D3 (Fri 22, Tue 26, Wed 27): idle ticks — 0 new orders,
       0 new exits, position still OPEN.
       D5 (Thu 28): horizon → exit materialized, position CLOSED,
       realized_pnl == 50, cash balance == 10050.

       Memorial Day Mon 25 is intentionally skipped (R7-B): firing
       there resolves to Fri 22 and is a no-op replay — a separate
       test exercises that rollback semantics.
    """
    from marketpulse.db.models import PaperCashLedger, PaperPosition
    from marketpulse.trading import daily_cycle
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.price_provider import ClosePrice

    D0 = date(2026, 5, 21)   # Thu
    D5 = date(2026, 5, 28)   # Thu, horizon

    # Seed one event on D0 14:00 NY = D0 18:00 UTC
    _seed_event(
        session, ticker="AAPL",
        event_time=datetime(2026, 5, 21, 18, 0, tzinfo=UTC),
        event_price=150.0,
    )

    fake_clock = FakeClock(now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC))
    allocator = _stub_allocator(horizon_date=D5)

    # Lock 6b+L1: exit price comes from PriceProvider.close_on_date on the
    # horizon date. Seed a ClosePrice so D5's exit tick has data to close.
    horizon_map = {
        ("AAPL", D5): ClosePrice(
            price=Decimal("155.000000"),
            price_date=D5,
            requested_date=D5,
            source="stub",
        ),
    }

    # === D0: place + tick (entry) ===
    r0 = daily_cycle.run(**_make_deps(
        session, clock=fake_clock, allocator=allocator,
        horizon_price_map=horizon_map,
    ))
    assert r0.tick_date == D0
    assert r0.orders_placed == 1
    assert r0.entries_materialized == 1
    assert r0.exits_materialized == 0
    assert r0.cycle_status == "completed"

    positions = session.execute(select(PaperPosition)).scalars().all()
    assert len(positions) == 1
    assert positions[0].status == "OPEN"
    assert positions[0].horizon_date == D5
    _assert_cash_invariant(session)

    # === D1-D3: idle (skipping Memorial Day Mon 5/25) ===
    idle_days = [
        date(2026, 5, 22),  # Fri
        date(2026, 5, 26),  # Tue (Mon 25 = Memorial Day)
        date(2026, 5, 27),  # Wed
    ]
    for d in idle_days:
        fake_clock.set(now=datetime(d.year, d.month, d.day, 21, 30, tzinfo=UTC))
        ri = daily_cycle.run(**_make_deps(
            session, clock=fake_clock, allocator=allocator,
            horizon_price_map=horizon_map,
        ))
        assert ri.tick_date == d, f"unexpected tick_date on {d}: {ri.tick_date}"
        assert ri.orders_placed == 0, (
            f"idle day {d} should not place orders; got {ri.orders_placed}"
        )
        assert ri.exits_materialized == 0, (
            f"idle day {d} should not exit; got {ri.exits_materialized}"
        )
        assert ri.cycle_status == "completed"
        _assert_cash_invariant(session)

    positions = session.execute(select(PaperPosition)).scalars().all()
    assert len(positions) == 1
    assert positions[0].status == "OPEN", "position should still be OPEN on D3"

    # === D5: horizon → exit ===
    fake_clock.set(now=datetime(2026, 5, 28, 21, 30, tzinfo=UTC))
    r5 = daily_cycle.run(**_make_deps(
        session, clock=fake_clock, allocator=allocator,
        horizon_price_map=horizon_map,
    ))
    assert r5.tick_date == D5
    assert r5.exits_materialized == 1
    assert r5.orders_placed == 0

    positions = session.execute(select(PaperPosition)).scalars().all()
    assert len(positions) == 1
    assert positions[0].status == "CLOSED"
    # PnL: (155 - 150) * 10 = 50
    assert positions[0].realized_pnl == Decimal("50")

    # Cash: 10000 - 1500 (entry) + 1550 (exit) = 10050
    cash_rows = session.execute(
        select(PaperCashLedger).order_by(PaperCashLedger.id),
    ).scalars().all()
    assert cash_rows[-1].balance_after == Decimal("10050")
    _assert_cash_invariant(session)


def test_cash_ledger_sum_equals_latest_balance(session):
    """Invariant: Σ paper_cash_ledger.delta == latest balance_after.
    Holds after the initial deposit and is asserted as an explicit
    primitive for callers / future regression."""
    from marketpulse.db.models import PaperCashLedger
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 21, tzinfo=UTC),
    )

    rows = session.execute(
        select(PaperCashLedger).order_by(PaperCashLedger.id),
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].delta == Decimal("10000")
    assert rows[0].balance_after == Decimal("10000")

    total = sum((r.delta for r in rows), start=Decimal("0"))
    assert total == rows[-1].balance_after


def test_tick_on_memorial_day_resolves_to_previous_friday(session):
    """R7-B rollback semantics. After D0 (Thu 21) + Fri (22) ticks,
    firing on Memorial Day Mon 25 resolves to Fri 22 via
    today_ny_trading_date's previous-session rollback. With Fri 22
    already TICK_COMPLETED, the rerun is a same-day replay: 0 new
    orders, 0 new TICK_COMPLETED rows, 0 SCHEDULER_GAP_DETECTED
    (sessions_after(Fri 22, Fri 22) == 0)."""
    from marketpulse.db.models import PaperAuditEvent, PaperOrder
    from marketpulse.trading import daily_cycle
    from marketpulse.trading.clock import FakeClock

    # Seed event on Thu D0 so D0 has something to place.
    _seed_event(
        session, ticker="AAPL",
        event_time=datetime(2026, 5, 21, 18, 0, tzinfo=UTC),
    )
    allocator = _stub_allocator(horizon_date=date(2026, 5, 28))

    # D0 Thu 21
    clock = FakeClock(now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC))
    daily_cycle.run(**_make_deps(
        session, clock=clock, allocator=allocator,
    ))

    # D1 Fri 22 — idle (no new events)
    clock.set(now=datetime(2026, 5, 22, 21, 30, tzinfo=UTC))
    r_fri = daily_cycle.run(**_make_deps(
        session, clock=clock, allocator=allocator,
    ))
    assert r_fri.tick_date == date(2026, 5, 22)

    orders_before = session.execute(select(PaperOrder)).scalars().all()
    n_orders_before = len(orders_before)
    audits_before = session.execute(
        select(PaperAuditEvent),
    ).scalars().all()
    n_tick_completed_before = sum(
        1 for a in audits_before if a.event_type == "TICK_COMPLETED"
    )
    n_gap_before = sum(
        1 for a in audits_before if a.event_type == "SCHEDULER_GAP_DETECTED"
    )

    # Fire on Memorial Day Mon 25 — should resolve to Fri 22 (rollback).
    clock.set(now=datetime(2026, 5, 25, 21, 30, tzinfo=UTC))
    r_mon = daily_cycle.run(**_make_deps(
        session, clock=clock, allocator=allocator,
    ))
    assert r_mon.tick_date == date(2026, 5, 22), (
        f"expected rollback to Fri 22; got {r_mon.tick_date}"
    )

    # No new state — TICK_COMPLETED is idempotent (lock 6a-L7).
    orders_after = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders_after) == n_orders_before
    audits_after = session.execute(
        select(PaperAuditEvent),
    ).scalars().all()
    n_tick_completed_after = sum(
        1 for a in audits_after if a.event_type == "TICK_COMPLETED"
    )
    n_gap_after = sum(
        1 for a in audits_after if a.event_type == "SCHEDULER_GAP_DETECTED"
    )
    assert n_tick_completed_after == n_tick_completed_before, (
        "Memorial Day replay should not emit a new TICK_COMPLETED"
    )
    assert n_gap_after == n_gap_before, (
        "Memorial Day replay should not emit SCHEDULER_GAP_DETECTED "
        "(sessions_after(Fri 22, Fri 22) == 0)"
    )


# === Phase 6b — composite gate E2E (op-tests #14, #16) ===

def test_e2e_phase6b_17_30_ny_happy_path(tmp_path, monkeypatch):
    """Op-test #14: Phase 6a default tick fires at 17:30 NY post-close;
    CompositeRiskGate's MarketHoursGate (post_close_until=18:00) must
    pass. This is the lock-iv compatibility check: 6b must not break the
    6a default scheduler cadence."""
    from datetime import UTC, date, datetime
    from decimal import Decimal
    from pathlib import Path

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from marketpulse.backtest.allocation import AllocationResult, AllocationWinner
    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperOrder
    from marketpulse.trading import daily_cycle
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.price_provider import StubPriceProvider
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gates import (
        RiskConfigProvider,
        build_standard_composite,
    )

    eng = create_engine(f"sqlite:///{tmp_path / 'e2e.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        repo = Repository(session=s)
        # 17:30 NY = 21:30 UTC on a Thursday (2026-05-21).
        clock = FakeClock(now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC))
        calendar = NYTradingCalendar()
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
        ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
        repo_root = Path(__file__).resolve().parents[2]
        provider = RiskConfigProvider.from_yaml(
            global_path=repo_root / "config" / "risk_gates.yaml",
            strategies_dir=repo_root / "marketpulse" / "strategies" / "definitions",
        )
        # E2E uses the production factory (lock 6b-L15) — this is also
        # the path the scheduler entrypoint runs in production.
        risk_gate = build_standard_composite(
            config_provider=provider, repository=repo,
            calendar=calendar, clock=clock,
            sector_provider=lambda t: "Technology" if t == "AAPL" else None,
        )
        engine = ForwardExecutionEngine(
            repository=repo, clock=clock, kill_switch=ks, risk_gate=risk_gate,
            price_provider=StubPriceProvider(map={}),
        )

        def alloc(**kw):
            return AllocationResult(
                winners=(
                    AllocationWinner(
                        strategy="momentum_breakout", ticker="AAPL",
                        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
                        event_price=150.0,
                        horizon_date=date(2026, 5, 28),
                        horizon_price=155.0,
                        quantity=10,
                        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
                        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
                        effective_corr_window=60,
                        rewarded_for_negative_corr=False,
                        would_change_rank=False,
                        size_clamped_by_override=False,
                        strategy_version="v1",
                    ),
                ),
                blocked=(), cash_used=1500.0, cash_remaining=8500.0,
                timeline=(),
            )
        alloc.__version__ = "v1"

        result = daily_cycle.run(
            clock=clock, engine=engine, repository=repo,
            bid_aggregator=BidAggregator(session=s, calendar=calendar),
            allocator=alloc, calendar=calendar, kill_switch=ks,
        )
        assert result.orders_placed == 1
        assert result.cycle_status == "completed"

        orders = s.execute(select(PaperOrder)).scalars().all()
        assert len(orders) == 1


def test_e2e_phase6b_sector_cap_denial_writes_per_gate_audit(tmp_path):
    """Op-test #16: E2E denial with all 4 gates active. Verifies the audit
    row carries failed_gates + per_gate."""
    from datetime import UTC, date, datetime
    from decimal import Decimal
    from pathlib import Path

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from marketpulse.backtest.allocation import AllocationResult, AllocationWinner
    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading import daily_cycle
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.price_provider import StubPriceProvider
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gates import (
        RiskConfigProvider,
        build_standard_composite,
    )

    eng = create_engine(f"sqlite:///{tmp_path / 'e2e2.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        repo = Repository(session=s)
        clock = FakeClock(now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC))
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
        ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
        repo_root = Path(__file__).resolve().parents[2]
        provider = RiskConfigProvider.from_yaml(
            global_path=repo_root / "config" / "risk_gates.yaml",
            strategies_dir=repo_root / "marketpulse" / "strategies" / "definitions",
        )
        # E2E uses the production factory (lock 6b-L15).
        risk_gate = build_standard_composite(
            config_provider=provider, repository=repo,
            calendar=NYTradingCalendar(), clock=clock,
            sector_provider=lambda t: "Technology",
        )
        engine = ForwardExecutionEngine(
            repository=repo, clock=clock, kill_switch=ks, risk_gate=risk_gate,
            price_provider=StubPriceProvider(map={}),
        )

        def alloc(**kw):
            return AllocationResult(
                winners=(
                    # event_price * quantity = 200 * 100 = 20_000, well over
                    # 0.35 * 10_000 = 3_500 cap → sector_exposure denies.
                    AllocationWinner(
                        strategy="momentum_breakout", ticker="AAPL",
                        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
                        event_price=200.0,
                        horizon_date=date(2026, 5, 28),
                        horizon_price=210.0,
                        quantity=100,
                        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
                        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
                        effective_corr_window=60,
                        rewarded_for_negative_corr=False,
                        would_change_rank=False,
                        size_clamped_by_override=False,
                        strategy_version="v1",
                    ),
                ),
                blocked=(), cash_used=0.0, cash_remaining=10000.0,
                timeline=(),
            )
        alloc.__version__ = "v1"

        result = daily_cycle.run(
            clock=clock, engine=engine, repository=repo,
            bid_aggregator=BidAggregator(session=s, calendar=NYTradingCalendar()),
            allocator=alloc, calendar=NYTradingCalendar(), kill_switch=ks,
        )
        assert result.orders_placed == 0
        assert result.orders_rejected == 1

        rejects = s.execute(
            select(PaperAuditEvent)
            .where(PaperAuditEvent.event_type == "ORDER_REJECTED")
        ).scalars().all()
        assert len(rejects) == 1
        ctx = rejects[0].context
        # strategy_size also denies (20_000 > 25_000? actually 20_000 < 25_000
        # so strategy_size approves; only sector_exposure denies).
        assert "sector_exposure" in ctx["failed_gates"]
        assert len(ctx["per_gate"]) == 4
