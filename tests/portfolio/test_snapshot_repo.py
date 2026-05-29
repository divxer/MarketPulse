# Layer: test
"""PR3a — snapshot_repo tests."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from marketpulse.portfolio.north_star import NavSnapshot
from marketpulse.portfolio.snapshot_repo import (
    SnapshotAlreadyExists,
    count_snapshots_in_window,
    force_replace_snapshot,
    get_earliest_snapshot,
    get_latest_snapshot,
    get_recent_snapshot_dates,
    get_snapshot,
    get_snapshot_series,
    get_spy_anchor,
    insert_snapshot,
)


def _make_snapshot(d: date, *, nav: str = "100000") -> NavSnapshot:
    return NavSnapshot(
        trading_date=d,
        cash_balance=Decimal(nav),
        holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal(nav),
        anchor_portfolio_nav=Decimal(nav),
        portfolio_index=Decimal("1"),
        spy_close=None,
        anchor_spy_close=None,
        spy_index=None,
        excess_return=None,
        trading_days_observed=1,
        coverage_ratio=Decimal("0.0111111111"),
        is_sufficient=False,
        unpriced_positions_count=0,
        unpriced_tickers=(),
    )


def test_insert_snapshot_succeeds(db_session):
    snap = _make_snapshot(date(2026, 5, 28))
    insert_snapshot(db_session, snap)
    db_session.commit()

    fetched = get_snapshot(db_session, date(2026, 5, 28))
    assert fetched is not None
    assert fetched.trading_date == date(2026, 5, 28)
    assert fetched.portfolio_nav == Decimal("100000")


def test_insert_snapshot_pk_conflict_raises(db_session):
    snap = _make_snapshot(date(2026, 5, 28), nav="100000")
    insert_snapshot(db_session, snap)
    db_session.commit()

    second = _make_snapshot(date(2026, 5, 28), nav="999999")
    with pytest.raises(SnapshotAlreadyExists):
        insert_snapshot(db_session, second)
    db_session.rollback()

    # original row preserved
    fetched = get_snapshot(db_session, date(2026, 5, 28))
    assert fetched.portfolio_nav == Decimal("100000")


def test_force_replace_snapshot(db_session):
    snap = _make_snapshot(date(2026, 5, 28), nav="100000")
    insert_snapshot(db_session, snap)
    db_session.commit()

    replacement = _make_snapshot(date(2026, 5, 28), nav="200000")
    force_replace_snapshot(db_session, replacement, reason="corp action backfill")
    db_session.commit()

    fetched = get_snapshot(db_session, date(2026, 5, 28))
    assert fetched.portfolio_nav == Decimal("200000")
    # Repo returns NavSnapshot dataclass which doesn't carry is_rebuilt;
    # verify via raw column read.
    from marketpulse.db.models import PaperNavSnapshot
    row = db_session.query(PaperNavSnapshot).filter_by(
        trading_date=date(2026, 5, 28),
    ).one()
    assert row.is_rebuilt is True
    assert row.rebuild_reason == "corp action backfill"
    assert row.updated_at != row.created_at


def test_get_latest_snapshot_empty(db_session):
    assert get_latest_snapshot(db_session) is None


def test_get_snapshot_series_range_ascending(db_session):
    for i in range(5):
        insert_snapshot(db_session, _make_snapshot(date(2026, 5, 24 + i)))
    db_session.commit()
    series = get_snapshot_series(
        db_session,
        window_start=date(2026, 5, 25),
        window_end=date(2026, 5, 27),
    )
    assert [s.trading_date for s in series] == [
        date(2026, 5, 25), date(2026, 5, 26), date(2026, 5, 27),
    ]


def test_get_recent_snapshot_dates_ascending(db_session):
    from datetime import timedelta as _td
    base = date(2026, 4, 1)
    for i in range(40):
        insert_snapshot(db_session, _make_snapshot(base + _td(days=i)))
    db_session.commit()
    dates = get_recent_snapshot_dates(db_session, limit=30)
    assert len(dates) == 30
    assert dates == sorted(dates)
    # Should be the most-recent 30 (last 30 calendar dates inserted).
    from datetime import timedelta
    expected_first = date(2026, 4, 1) + timedelta(days=40 - 30)
    assert dates[0] == expected_first


def test_count_snapshots_in_window_caps_at_size(db_session):
    """200 snapshots → window_size=90 returns 90 (trading-day cap, L11)."""
    from datetime import timedelta
    base = date(2026, 1, 1)
    for i in range(200):
        insert_snapshot(db_session, _make_snapshot(base + timedelta(days=i)))
    db_session.commit()
    count = count_snapshots_in_window(
        db_session, window_end=base + timedelta(days=199), window_size=90,
    )
    assert count == 90


def test_count_snapshots_in_window_below_cap(db_session):
    """12 snapshots → window_size=90 returns 12."""
    from datetime import timedelta
    base = date(2026, 1, 1)
    for i in range(12):
        insert_snapshot(db_session, _make_snapshot(base + timedelta(days=i)))
    db_session.commit()
    count = count_snapshots_in_window(
        db_session, window_end=base + timedelta(days=11), window_size=90,
    )
    assert count == 12


def test_get_spy_anchor_none_when_no_anchors(db_session):
    insert_snapshot(db_session, _make_snapshot(date(2026, 5, 28)))
    db_session.commit()
    assert get_spy_anchor(db_session) is None


def test_get_spy_anchor_returns_earliest_non_null(db_session):
    """L16: earliest snapshot with non-null anchor_spy_close."""
    from marketpulse.db.models import PaperNavSnapshot

    # Day 1: no SPY (null anchor)
    insert_snapshot(db_session, _make_snapshot(date(2026, 5, 26)))
    # Day 2: SPY available — set anchor_spy_close explicitly
    insert_snapshot(db_session, _make_snapshot(date(2026, 5, 27)))
    row = db_session.get(PaperNavSnapshot, date(2026, 5, 27))
    row.anchor_spy_close = Decimal("500")
    row.spy_close = Decimal("500")
    # Day 3: also SPY available
    insert_snapshot(db_session, _make_snapshot(date(2026, 5, 28)))
    row3 = db_session.get(PaperNavSnapshot, date(2026, 5, 28))
    row3.anchor_spy_close = Decimal("500")
    row3.spy_close = Decimal("505")
    db_session.commit()

    anchor = get_spy_anchor(db_session)
    assert anchor == Decimal("500")


def test_get_earliest_snapshot_empty(db_session):
    assert get_earliest_snapshot(db_session) is None


def test_get_earliest_snapshot_returns_first(db_session):
    insert_snapshot(db_session, _make_snapshot(date(2026, 5, 27), nav="111"))
    insert_snapshot(db_session, _make_snapshot(date(2026, 5, 26), nav="222"))
    insert_snapshot(db_session, _make_snapshot(date(2026, 5, 28), nav="333"))
    db_session.commit()
    earliest = get_earliest_snapshot(db_session)
    assert earliest.trading_date == date(2026, 5, 26)
    assert earliest.portfolio_nav == Decimal("222")
