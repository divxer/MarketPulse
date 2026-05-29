# Layer: test
"""PR3a — snapshot_repo tests."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from marketpulse.portfolio.north_star import NavSnapshot
from marketpulse.portfolio.snapshot_repo import (
    SnapshotAlreadyExists,
    force_replace_snapshot,
    get_latest_snapshot,
    get_snapshot,
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
