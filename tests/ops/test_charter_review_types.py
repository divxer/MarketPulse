# Layer: test
"""PR3b — charter_review_types smoke (L18 single-source-of-truth)."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from marketpulse.ops.charter_review_types import (
    CharterReviewPayload,
    DiagnosticsWeek,
    DiagnosticWeek,
    NorthStarWeek,
    OperationalFloor,
    ReasonCount,
    SnapshotAppendix,
    WeekWindow,
)


def _empty_diag() -> DiagnosticWeek:
    return DiagnosticWeek(value=None, observations=0, top_reasons=())


def _empty_diags() -> DiagnosticsWeek:
    return DiagnosticsWeek(
        tick_success_rate=_empty_diag(),
        order_rejection_rate=_empty_diag(),
        paper_trade_count=_empty_diag(),
        engine_invariant_errors=_empty_diag(),
    )


def _empty_window(week_end: date) -> WeekWindow:
    from datetime import timedelta
    return WeekWindow(
        week_start=week_end - timedelta(days=6),
        week_end=week_end,
        trading_days_observed=0,
    )


def _empty_north_star(week_end: date) -> NorthStarWeek:
    return NorthStarWeek(
        week=_empty_window(week_end),
        first_snapshot_date=None,
        last_snapshot_date=None,
        excess_return_end=None,
        portfolio_index_end=None,
        spy_index_end=None,
        coverage_ratio_end=None,
        is_sufficient_end=False,
    )


def _empty_op_floor() -> OperationalFloor:
    return OperationalFloor(
        backup_status="missing", backup_is_stale=True,
        backup_last_at=None, backup_error=None,
        manifest_available=False,
    )


def _empty_appendix() -> SnapshotAppendix:
    return SnapshotAppendix(
        trading_date=None, cash_balance=None, holdings_mtm=None,
        portfolio_nav=None, unpriced_positions_count=0,
        unpriced_tickers=(),
    )


def _empty_payload(week_end: date) -> CharterReviewPayload:
    from datetime import timedelta
    prior_end = week_end - timedelta(days=7)
    return CharterReviewPayload(
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
        week_ending=week_end,
        this_week=_empty_window(week_end),
        prior_week=_empty_window(prior_end),
        north_star_this=_empty_north_star(week_end),
        north_star_prior=_empty_north_star(prior_end),
        diagnostics_this=_empty_diags(),
        diagnostics_prior=_empty_diags(),
        operational_floor=_empty_op_floor(),
        appendix_snapshot=_empty_appendix(),
    )


def test_reason_count_frozen():
    r = ReasonCount(reason="abc", count=3)
    with pytest.raises(FrozenInstanceError):
        r.reason = "xyz"  # type: ignore[misc]


def test_payload_frozen():
    p = _empty_payload(date(2026, 8, 16))
    with pytest.raises(FrozenInstanceError):
        p.week_ending = date(2026, 8, 9)  # type: ignore[misc]


def test_diagnostic_week_value_decimal_or_int_or_none():
    DiagnosticWeek(value=Decimal("0.95"), observations=20, top_reasons=())
    DiagnosticWeek(value=5, observations=5, top_reasons=())
    DiagnosticWeek(value=None, observations=0, top_reasons=())
