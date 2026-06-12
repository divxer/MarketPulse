# Layer: orchestration
"""NAV final-only price lookup + provisional-fallback diagnostics (spec §5)."""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal

from marketpulse.db.models import PaperCashLedger, PriceCacheEntry
from marketpulse.portfolio.snapshot_runner import _read_price_lookup, run_nav_snapshot


def _seed(db_session, ticker, d, close, *, is_final):
    db_session.add(PriceCacheEntry(
        ticker=ticker, date=d, open=close, high=close, low=close,
        close=close, volume=1,
        fetched_at=datetime(2026, 6, 11, 12, 0, tzinfo=UTC),
        is_final=is_final,
        finalized_at=datetime(2026, 6, 11, 12, 0, tzinfo=UTC) if is_final else None,
    ))
    db_session.commit()


def test_wrong_shape_regression_provisional_max_falls_back_to_final(db_session, caplog):
    """MANDATORY (spec §5/review): 06-11 provisional + 06-10 final,
    trading_date=06-11 → MUST return the 06-10 close, not None. Catches the
    join-only-filter bug where the subquery picks the provisional max(date)."""
    _seed(db_session, "SPY", date(2026, 6, 10), 725.10, is_final=True)
    _seed(db_session, "SPY", date(2026, 6, 11), 730.72, is_final=False)
    lookup, spy_close, fallback = _read_price_lookup(db_session, date(2026, 6, 11))
    assert spy_close is not None
    assert float(spy_close) == 725.10
    assert "SPY" in fallback

    # Wiring assertion (plan Step 5): run_nav_snapshot emits the
    # nav_provisional_fallback log for consumed tickers (SPY always consumed).
    db_session.add(PaperCashLedger(
        timestamp=datetime(2026, 6, 1, tzinfo=UTC),
        delta=Decimal("10000"),
        reason="INITIAL_DEPOSIT",
        fill_id=None,
        balance_after=Decimal("10000"),
    ))
    db_session.commit()
    with caplog.at_level(
        logging.INFO, logger="marketpulse.portfolio.snapshot_runner",
    ):
        run_nav_snapshot(db_session, trading_date=date(2026, 6, 11))
    records = [
        r for r in caplog.records if r.getMessage() == "nav_provisional_fallback"
    ]
    assert len(records) == 1
    assert records[0].provisional_fallback_count == 1
    assert records[0].provisional_fallback_tickers == ["SPY"]


def test_all_final_uses_latest(db_session):
    _seed(db_session, "SPY", date(2026, 6, 10), 725.10, is_final=True)
    _seed(db_session, "SPY", date(2026, 6, 11), 728.00, is_final=True)
    lookup, spy_close, fallback = _read_price_lookup(db_session, date(2026, 6, 11))
    assert float(spy_close) == 728.00
    assert fallback == []


def test_all_provisional_ticker_is_unpriced(db_session):
    _seed(db_session, "QBTS", date(2026, 6, 11), 23.60, is_final=False)
    lookup, _, fallback = _read_price_lookup(db_session, date(2026, 6, 11))
    assert lookup("QBTS") is None
    assert "QBTS" in fallback
