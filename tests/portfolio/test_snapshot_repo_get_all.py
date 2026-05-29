# Layer: test
"""PR4 — get_all_snapshots read-only helper."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketpulse.db.base import Base
from marketpulse.portfolio.north_star import NavSnapshot
from marketpulse.portfolio.snapshot_repo import get_all_snapshots, insert_snapshot


def _snap(d: date) -> NavSnapshot:
    return NavSnapshot(
        trading_date=d, cash_balance=Decimal("100000"), holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal("100000"), anchor_portfolio_nav=Decimal("100000"),
        portfolio_index=Decimal("1.0"), spy_close=Decimal("500"),
        anchor_spy_close=Decimal("500"), spy_index=Decimal("1.0"),
        excess_return=Decimal("0.0"), trading_days_observed=1,
        coverage_ratio=Decimal("0.01"), is_sufficient=False,
        unpriced_positions_count=0, unpriced_tickers=(),
    )


def test_get_all_snapshots_empty_returns_empty_list():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        assert get_all_snapshots(s) == []


def test_get_all_snapshots_ascending_by_date():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        insert_snapshot(s, _snap(date(2026, 8, 14)))
        insert_snapshot(s, _snap(date(2026, 8, 12)))
        insert_snapshot(s, _snap(date(2026, 8, 13)))
        s.commit()
        rows = get_all_snapshots(s)
        assert [r.trading_date for r in rows] == [
            date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14),
        ]
