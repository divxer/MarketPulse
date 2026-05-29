# Layer: test
"""PR3a — charter_metrics north_star + diagnostics extension tests."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from marketpulse.db.models import PaperNavSnapshot
from marketpulse.ops.charter_metrics import build_charter_metrics
from marketpulse.portfolio.north_star import NavSnapshot
from marketpulse.portfolio.snapshot_repo import insert_snapshot


def _snap(d: date, *, value: str = "0.032", observed: int = 12) -> NavSnapshot:
    return NavSnapshot(
        trading_date=d,
        cash_balance=Decimal("100000"),
        holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal("100000"),
        anchor_portfolio_nav=Decimal("100000"),
        portfolio_index=Decimal("1.041"),
        spy_close=Decimal("500"),
        anchor_spy_close=Decimal("475"),
        spy_index=Decimal("1.009"),
        excess_return=Decimal(value),
        trading_days_observed=observed,
        coverage_ratio=Decimal(observed) / Decimal("90"),
        is_sufficient=observed >= 90,
        unpriced_positions_count=0,
        unpriced_tickers=(),
    )


def test_north_star_empty_table(db_session, tmp_path):
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    ns = result["north_star"]
    assert ns["error"] == "no_snapshots_yet"
    assert ns["value"] is None
    assert ns["coverage_ratio"] == 0
    assert ns["is_sufficient"] is False
    assert ns["data_quality"]["is_complete"] is True


def test_north_star_partial_window(db_session, tmp_path):
    for i in range(12):
        insert_snapshot(db_session, _snap(date(2026, 7, 30) + timedelta(days=i),
                                           observed=i + 1))
    db_session.commit()

    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    ns = result["north_star"]
    assert ns["error"] is None
    assert ns["value"] == 0.032
    assert isinstance(ns["value"], float)  # L17
    assert ns["portfolio_index"] == 1.041
    assert ns["spy_index"] == 1.009
    assert ns["is_sufficient"] is False
    assert ns["trading_days_observed"] == 12
    assert ns["window_start"] == "2026-07-30"
    assert ns["window_end"] == "2026-08-10"
    assert ns["data_quality"]["is_complete"] is True
    assert ns["data_quality"]["unpriced_positions_count"] == 0


def test_north_star_sufficient_window(db_session, tmp_path):
    insert_snapshot(db_session, _snap(date(2026, 8, 14), observed=90))
    db_session.commit()
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    assert result["north_star"]["is_sufficient"] is True
    assert result["north_star"]["coverage_ratio"] == 1.0


def test_north_star_session_none(tmp_path):
    """L10: session=None → db_session_unavailable fallback."""
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=None,
    )
    ns = result["north_star"]
    assert ns["error"] == "db_session_unavailable"
    assert ns["value"] is None


def test_north_star_data_quality_is_complete_false(db_session, tmp_path):
    """Snapshot with unpriced positions → is_complete=False."""
    snap = _snap(date(2026, 8, 14), observed=12)
    insert_snapshot(db_session, NavSnapshot(
        **{**snap.__dict__, "unpriced_positions_count": 1,
           "unpriced_tickers": ("XYZ",)},
    ))
    db_session.commit()
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    dq = result["north_star"]["data_quality"]
    assert dq["is_complete"] is False
    assert dq["unpriced_positions_count"] == 1
    assert dq["unpriced_tickers"] == ["XYZ"]
