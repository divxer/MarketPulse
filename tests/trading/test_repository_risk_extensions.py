# Layer: stateful
"""6b-T8/T9: Repository extensions for risk gates."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'repo.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _insert_order(session, order_id, strategy="momentum_breakout", ticker="AAPL"):
    from marketpulse.db.models import PaperOrder
    o = PaperOrder(
        id=order_id, strategy=strategy, ticker=ticker, quantity=10,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150.00"),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155.00"),
        idempotency_key=f"k-{order_id}",
        allocation_run_id=f"r-{order_id}",
        status="ENTRY_FILLED",
        placed_at=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        filled_at=datetime(2026, 5, 21, 14, 1, tzinfo=UTC),
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
    session.add(o)
    session.flush()
    return o


def _insert_fill(session, order_id, side, realized_pnl, filled_at):
    from marketpulse.db.models import PaperFill, PaperPosition
    # PaperFill.position_id is NOT NULL — create a backing position per
    # (order_id, side) pair. Get-or-create so the same order can host
    # both ENTRY and EXIT fills on the same position row.
    pos = session.query(PaperPosition).filter_by(order_id=order_id).first()
    if pos is None:
        pos = PaperPosition(
            order_id=order_id,
            strategy="momentum_breakout",
            ticker="AAPL",
            quantity=10,
            entry_price=Decimal("150.00"),
            entry_date=filled_at.date(),
            horizon_date=filled_at.date() + timedelta(days=7),
            status="OPEN",
            opened_at=filled_at,
        )
        session.add(pos)
        session.flush()
    f = PaperFill(
        order_id=order_id, position_id=pos.id, side=side, quantity=10,
        price=Decimal("150.00"), filled_at=filled_at,
        cash_delta=Decimal("0"),
        realized_pnl=realized_pnl,
    )
    session.add(f)
    session.flush()
    return f


def test_today_realized_pnl_no_fills_returns_zero(session):
    from marketpulse.trading.repository import Repository
    repo = Repository(session=session)
    assert repo.today_realized_pnl(tick_date=date(2026, 5, 21)) == Decimal(0)


def test_today_realized_pnl_sums_exit_fills_in_ny_day(session):
    from marketpulse.trading.repository import Repository
    NY = ZoneInfo("America/New_York")
    # Two EXIT fills on 2026-05-21 NY (Thursday).
    # 14:00 NY = 18:00 UTC; 17:30 NY = 21:30 UTC.
    o1 = _insert_order(session, 1001)
    o2 = _insert_order(session, 1002)
    _insert_fill(session, o1.id, "EXIT", Decimal("100"),
                 datetime(2026, 5, 21, 14, 0, tzinfo=NY).astimezone(UTC))
    _insert_fill(session, o2.id, "EXIT", Decimal("-30"),
                 datetime(2026, 5, 21, 17, 30, tzinfo=NY).astimezone(UTC))
    repo = Repository(session=session)
    assert repo.today_realized_pnl(tick_date=date(2026, 5, 21)) == Decimal(70)


def test_today_realized_pnl_excludes_entry_fills(session):
    """ENTRY fills don't realize PnL; gate sums only EXIT fills."""
    from marketpulse.trading.repository import Repository
    NY = ZoneInfo("America/New_York")
    o = _insert_order(session, 2001)
    _insert_fill(session, o.id, "ENTRY", None,
                 datetime(2026, 5, 21, 14, 0, tzinfo=NY).astimezone(UTC))
    _insert_fill(session, o.id, "EXIT", Decimal("50"),
                 datetime(2026, 5, 21, 15, 0, tzinfo=NY).astimezone(UTC))
    repo = Repository(session=session)
    assert repo.today_realized_pnl(tick_date=date(2026, 5, 21)) == Decimal(50)


def test_today_realized_pnl_excludes_prior_day_fills(session):
    from marketpulse.trading.repository import Repository
    NY = ZoneInfo("America/New_York")
    o1 = _insert_order(session, 3001)
    o2 = _insert_order(session, 3002)
    # Yesterday's EXIT.
    _insert_fill(session, o1.id, "EXIT", Decimal("100"),
                 datetime(2026, 5, 20, 14, 0, tzinfo=NY).astimezone(UTC))
    # Today's EXIT.
    _insert_fill(session, o2.id, "EXIT", Decimal("25"),
                 datetime(2026, 5, 21, 14, 0, tzinfo=NY).astimezone(UTC))
    repo = Repository(session=session)
    assert repo.today_realized_pnl(tick_date=date(2026, 5, 21)) == Decimal(25)


def test_today_realized_pnl_dst_spring_forward_no_overlap(session):
    """Lock 6b-L13: on 2026-03-08 (US spring-forward), the NY day is 23h
    long in wall-clock; we must NOT include a fill that lands in the next
    NY-day's wall-clock window. Build both bounds in NY-local time first."""
    from marketpulse.trading.repository import Repository
    NY = ZoneInfo("America/New_York")
    o1 = _insert_order(session, 4001)
    o2 = _insert_order(session, 4002)
    # Sunday 2026-03-08 NY ends at 2026-03-09 00:00 NY = 2026-03-09 04:00 UTC.
    # A naïve +24h-from-NY-midnight-UTC window would extend to 05:00 UTC
    # and accidentally include a fill at 2026-03-09 00:30 NY (04:30 UTC).
    _insert_fill(session, o1.id, "EXIT", Decimal("10"),
                 datetime(2026, 3, 8, 23, 30, tzinfo=NY).astimezone(UTC))
    _insert_fill(session, o2.id, "EXIT", Decimal("999"),
                 datetime(2026, 3, 9, 0, 30, tzinfo=NY).astimezone(UTC))
    repo = Repository(session=session)
    # Only the 23:30 NY fill should be counted for 2026-03-08.
    assert repo.today_realized_pnl(tick_date=date(2026, 3, 8)) == Decimal(10)
    # The 00:30 next-day fill counts for 2026-03-09.
    assert repo.today_realized_pnl(tick_date=date(2026, 3, 9)) == Decimal(999)


def _insert_position(
    session, *, ticker, quantity, entry_price,
    status="OPEN", strategy="momentum_breakout",
):
    """Helper — minimal OPEN position. order_id reused to avoid wiring a
    second order; CHECK constraints don't gate on order_id uniqueness across
    OPEN rows (only the order table enforces that via unique idx)."""
    from marketpulse.db.models import PaperOrder, PaperPosition
    # Each PaperPosition needs a distinct order_id (UNIQUE). Insert a stub
    # order first.
    nxt = (session.execute(
        __import__("sqlalchemy").select(__import__("sqlalchemy").func.coalesce(
            __import__("sqlalchemy").func.max(PaperOrder.id), 0
        ))
    ).scalar() or 0) + 1
    o = PaperOrder(
        id=nxt, strategy=strategy, ticker=ticker, quantity=quantity,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=entry_price,
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("0"),
        idempotency_key=f"pos-{nxt}-{ticker}",
        allocation_run_id=f"r-{nxt}",
        status="ENTRY_FILLED",
        placed_at=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        filled_at=datetime(2026, 5, 21, 14, 1, tzinfo=UTC),
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
    session.add(o)
    session.flush()
    # ck_paper_position_closed_both_set: CLOSED rows need both fill_ids
    # set. Use stub integers (we're not exercising the FK relation).
    if status == "CLOSED":
        p = PaperPosition(
            order_id=o.id, strategy=strategy, ticker=ticker,
            quantity=quantity, entry_price=entry_price,
            entry_date=date(2026, 5, 21),
            horizon_date=date(2026, 5, 28),
            status=status,
            opened_at=datetime(2026, 5, 21, 14, 1, tzinfo=UTC),
            entry_fill_id=nxt, exit_fill_id=nxt + 10_000,
            closed_at=datetime(2026, 5, 21, 15, 0, tzinfo=UTC),
            exit_price=entry_price,
            realized_pnl=Decimal("0"),
        )
    else:
        p = PaperPosition(
            order_id=o.id, strategy=strategy, ticker=ticker,
            quantity=quantity, entry_price=entry_price,
            entry_date=date(2026, 5, 21),
            horizon_date=date(2026, 5, 28),
            status=status,
            opened_at=datetime(2026, 5, 21, 14, 1, tzinfo=UTC),
            entry_fill_id=None, exit_fill_id=None,
        )
    session.add(p)
    session.flush()
    return p


def test_sector_exposure_notional_empty(session):
    from marketpulse.trading.repository import Repository
    repo = Repository(session=session)
    result = repo.sector_exposure_notional(sector_provider=lambda t: "Technology")
    assert result == {}


def test_sector_exposure_notional_groups_open_positions(session):
    from marketpulse.trading.repository import Repository
    _insert_position(session, ticker="AAPL", quantity=10, entry_price=Decimal("150"))
    _insert_position(session, ticker="MSFT", quantity=5, entry_price=Decimal("400"))
    _insert_position(session, ticker="JPM", quantity=20, entry_price=Decimal("160"))

    def sector(t):
        return {"AAPL": "Technology", "MSFT": "Technology", "JPM": "Financials"}[t]

    repo = Repository(session=session)
    result = repo.sector_exposure_notional(sector_provider=sector)
    assert result["Technology"] == Decimal("3500")   # 10*150 + 5*400
    assert result["Financials"] == Decimal("3200")   # 20*160


def test_sector_exposure_notional_excludes_closed_positions(session):
    from marketpulse.trading.repository import Repository
    _insert_position(session, ticker="AAPL", quantity=10, entry_price=Decimal("150"))
    _insert_position(
        session, ticker="OLD", quantity=10,
        entry_price=Decimal("100"), status="CLOSED",
    )

    repo = Repository(session=session)
    result = repo.sector_exposure_notional(sector_provider=lambda t: "Technology")
    assert result == {"Technology": Decimal("1500")}


def test_sector_exposure_notional_excludes_unknown_sector_positions(session):
    """Lock 6b-L8 / 6b-L11: positions with sector_provider(t) == None do
    not anchor a sector bucket. They're silently dropped from the result;
    SectorExposureGate's own pre-check fails NEW orders with unknown sector
    via the fail-closed `proposed_sector is None` path."""
    from marketpulse.trading.repository import Repository
    _insert_position(session, ticker="KNOWN", quantity=10, entry_price=Decimal("100"))
    _insert_position(session, ticker="UNKNOWN", quantity=5, entry_price=Decimal("200"))

    def sector(t):
        return "Tech" if t == "KNOWN" else None

    repo = Repository(session=session)
    result = repo.sector_exposure_notional(sector_provider=sector)
    assert result == {"Tech": Decimal("1000")}
