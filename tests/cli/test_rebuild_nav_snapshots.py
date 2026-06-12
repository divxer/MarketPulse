# Layer: cli
"""One-off rebuild of provisional-contaminated NAV snapshots (spec §6)."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from marketpulse.cli.rebuild_nav_snapshots import REBUILD_REASON, rebuild
from marketpulse.db.models import PaperNavSnapshot
from tests.portfolio.test_snapshot_runner import _seed_cash, _seed_price

HEALED_CLOSE = 725.10
CONTAMINATED_CLOSE = "730.72"


def _insert_contaminated_snapshot(session, d: date) -> None:
    """Pre-insert a 'contaminated' snapshot row (spy_close from a midday bar)."""
    session.add(PaperNavSnapshot(
        trading_date=d,
        cash_balance=Decimal("100000"),
        holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal("100000"),
        anchor_portfolio_nav=Decimal("100000"),
        portfolio_index=Decimal("1"),
        spy_close=Decimal(CONTAMINATED_CLOSE),
        anchor_spy_close=Decimal(CONTAMINATED_CLOSE),
        spy_index=Decimal("1"),
        excess_return=Decimal("0"),
        trading_days_observed=1,
        coverage_ratio=Decimal("0.011"),
        is_sufficient=False,
        unpriced_positions_count=0,
        unpriced_tickers=None,
        created_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
        updated_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
        is_rebuilt=False,
        rebuild_reason=None,
    ))
    session.flush()


def test_rebuild_order_and_flags(db_session, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "marketpulse.cli.rebuild_nav_snapshots.finalize_provisional_bars",
        lambda session: calls.append("finalize"),
    )

    _seed_cash(db_session, "100000", datetime(2026, 6, 9, 13, 0, tzinfo=UTC))
    # HEALED final closes — finalize already ran in production by rebuild time;
    # here the recorder lambda stands in and the fixture seeds final bars.
    _seed_price(db_session, "SPY", date(2026, 6, 10), HEALED_CLOSE)
    _seed_price(db_session, "SPY", date(2026, 6, 11), 727.50)
    _insert_contaminated_snapshot(db_session, date(2026, 6, 10))
    _insert_contaminated_snapshot(db_session, date(2026, 6, 11))
    db_session.commit()

    rebuild(db_session, dates=(date(2026, 6, 10), date(2026, 6, 11)))

    # Finalize ran exactly once, FIRST — before any snapshot work.
    assert calls == ["finalize"]

    rows = db_session.scalars(
        select(PaperNavSnapshot).order_by(PaperNavSnapshot.trading_date),
    ).all()
    assert [r.trading_date for r in rows] == [date(2026, 6, 10), date(2026, 6, 11)]
    for row in rows:
        assert row.is_rebuilt is True
        assert row.rebuild_reason == REBUILD_REASON == "provisional_price_cache_fix"
    # 06-10 spy_close reflects the healed final close, not the midday 730.72.
    assert float(rows[0].spy_close) == HEALED_CLOSE


def test_rebuild_skips_missing_date_gracefully(db_session, monkeypatch):
    monkeypatch.setattr(
        "marketpulse.cli.rebuild_nav_snapshots.finalize_provisional_bars",
        lambda session: None,
    )
    # No snapshot, no cash ledger → NoCashLedgerForDate caught per-date.
    rebuild(db_session, dates=(date(2026, 6, 10),))


def test_rebuild_failure_preserves_old_snapshot(db_session, monkeypatch):
    monkeypatch.setattr(
        "marketpulse.cli.rebuild_nav_snapshots.finalize_provisional_bars",
        lambda session: None,
    )
    d = date(2026, 6, 10)
    _insert_contaminated_snapshot(db_session, d)
    db_session.commit()

    # No cash ledger rows: delete+recompute fails → rollback restores old row.
    rebuild(db_session, dates=(d,))

    row = db_session.get(PaperNavSnapshot, d)
    assert row is not None
    assert row.is_rebuilt is False
