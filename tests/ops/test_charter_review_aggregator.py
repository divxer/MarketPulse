# Layer: test
"""PR3b — charter_review_aggregator tests."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from marketpulse.ops.charter_review_aggregator import (
    _week_window,
    build_payload,
)
from marketpulse.ops.charter_review_types import CharterReviewPayload


def test_week_window_sunday_to_monday():
    """Sunday Aug 16 2026 → week_start Mon Aug 10."""
    w = _week_window(date(2026, 8, 16))
    assert w.week_start == date(2026, 8, 10)
    assert w.week_end == date(2026, 8, 16)
    assert w.trading_days_observed == 0  # filled later


def test_build_payload_empty_db(db_session):
    payload = build_payload(
        session=db_session,
        week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    assert isinstance(payload, CharterReviewPayload)
    assert payload.week_ending == date(2026, 8, 16)
    assert payload.this_week.trading_days_observed == 0
    assert payload.prior_week.trading_days_observed == 0
    # All diagnostic values None on empty DB.
    for d in (
        payload.diagnostics_this.tick_success_rate,
        payload.diagnostics_this.order_rejection_rate,
        payload.diagnostics_this.paper_trade_count,
        payload.diagnostics_this.engine_invariant_errors,
    ):
        assert d.value is None
        assert d.observations == 0
        assert d.top_reasons == ()
    # Manifest None → L14
    op = payload.operational_floor
    assert op.manifest_available is False
    assert op.backup_status == "missing"
    assert op.backup_is_stale is True
    assert op.backup_last_at is None
    assert op.backup_error is None
    # Appendix empty.
    assert payload.appendix_snapshot.trading_date is None
