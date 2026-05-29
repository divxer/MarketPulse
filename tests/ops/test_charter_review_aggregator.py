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


from marketpulse.db.models import (
    PaperAuditEvent,
    PaperFill,
    PaperNavSnapshot,
    PaperOrder,
    PaperPosition,
)


def _seed_snapshot(session, d: date, **overrides):
    row = PaperNavSnapshot(
        trading_date=d,
        cash_balance=overrides.get("cash_balance", Decimal("100000")),
        holdings_mtm=overrides.get("holdings_mtm", Decimal("0")),
        portfolio_nav=overrides.get("portfolio_nav", Decimal("100000")),
        anchor_portfolio_nav=overrides.get("anchor_portfolio_nav", Decimal("100000")),
        portfolio_index=overrides.get("portfolio_index", Decimal("1")),
        spy_close=overrides.get("spy_close"),
        anchor_spy_close=overrides.get("anchor_spy_close"),
        spy_index=overrides.get("spy_index"),
        excess_return=overrides.get("excess_return"),
        trading_days_observed=overrides.get("trading_days_observed", 1),
        coverage_ratio=overrides.get("coverage_ratio", Decimal("0.011")),
        is_sufficient=overrides.get("is_sufficient", False),
        unpriced_positions_count=overrides.get("unpriced_positions_count", 0),
        unpriced_tickers=overrides.get("unpriced_tickers"),
        created_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
        updated_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
        is_rebuilt=False,
        rebuild_reason=None,
    )
    session.add(row)
    session.flush()


def _seed_audit(session, *, ts: datetime, event_type: str, reason: str = ""):
    session.add(PaperAuditEvent(
        timestamp=ts, event_type=event_type,
        order_id=None, strategy=None, reason=reason, context={},
    ))


def test_build_payload_trading_days_observed(db_session):
    for d in (date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)):
        _seed_snapshot(db_session, d)
    for d in (date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5),
              date(2026, 8, 6), date(2026, 8, 7)):
        _seed_snapshot(db_session, d)
    db_session.commit()

    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    assert payload.this_week.trading_days_observed == 3
    assert payload.prior_week.trading_days_observed == 5


def test_build_payload_week_window_inclusive(db_session):
    _seed_snapshot(db_session, date(2026, 8, 10))   # Mon
    _seed_snapshot(db_session, date(2026, 8, 16))   # Sun
    _seed_snapshot(db_session, date(2026, 8, 17))   # next-Mon, excluded
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    assert payload.this_week.trading_days_observed == 2


def test_build_payload_north_star_first_last(db_session):
    _seed_snapshot(db_session, date(2026, 8, 10),
                   excess_return=Decimal("0.005"), portfolio_index=Decimal("1.005"))
    _seed_snapshot(db_session, date(2026, 8, 13),
                   excess_return=Decimal("0.018"), portfolio_index=Decimal("1.018"))
    _seed_snapshot(db_session, date(2026, 8, 14),
                   excess_return=Decimal("0.032"), portfolio_index=Decimal("1.041"))
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    ns = payload.north_star_this
    assert ns.first_snapshot_date == date(2026, 8, 10)
    assert ns.last_snapshot_date == date(2026, 8, 14)
    assert ns.excess_return_end == Decimal("0.032")


def test_build_payload_tick_success_rate(db_session):
    base = datetime(2026, 8, 10, tzinfo=UTC)
    for i in range(18):
        _seed_audit(db_session, ts=base + timedelta(hours=i),
                    event_type="TICK_COMPLETED")
    for i in range(2):
        _seed_audit(db_session, ts=base + timedelta(days=1, hours=i),
                    event_type="ENGINE_INVARIANT_ERROR", reason="allocator_failed")
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    diag = payload.diagnostics_this.tick_success_rate
    assert diag.value == Decimal("18") / Decimal("20")
    assert diag.observations == 20
    assert len(diag.top_reasons) == 1
    assert diag.top_reasons[0].reason == "allocator_failed"
    assert diag.top_reasons[0].count == 2


def test_build_payload_rejection_top_reasons_sorted(db_session):
    base = datetime(2026, 8, 10, tzinfo=UTC)
    for i in range(10):
        _seed_audit(db_session, ts=base + timedelta(minutes=i),
                    event_type="ORDER_PLACED")
    plan = (("a", 5), ("b", 3), ("c", 3), ("d", 1), ("e", 1))
    j = 0
    for reason, n in plan:
        for _ in range(n):
            _seed_audit(db_session, ts=base + timedelta(hours=1, minutes=j),
                        event_type="ORDER_REJECTED", reason=reason)
            j += 1
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    diag = payload.diagnostics_this.order_rejection_rate
    assert tuple((r.reason, r.count) for r in diag.top_reasons) == (
        ("a", 5), ("b", 3), ("c", 3),
    )
    # decisions = 10 placed + 13 rejected = 23 → rejected/decisions
    assert diag.value == Decimal("13") / Decimal("23")
    assert diag.observations == 23


def test_build_payload_trade_count_uses_fills(db_session):
    # Seed a snapshot so observations > 0 (otherwise value=None per spec L22).
    _seed_snapshot(db_session, date(2026, 8, 10))
    base = datetime(2026, 8, 10, tzinfo=UTC)
    # Audit ORDER_ENTRY_FILLED present but NO paper_fill ENTRY rows.
    _seed_audit(db_session, ts=base, event_type="ORDER_ENTRY_FILLED")
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    # L5: source is paper_fill, not audit event. Snapshot exists → obs>0.
    assert payload.diagnostics_this.paper_trade_count.value == 0
    assert payload.diagnostics_this.paper_trade_count.observations == 1


def test_build_payload_trade_count_none_when_no_snapshots(db_session):
    """L22: zero observations → value=None (NOT 0)."""
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    assert payload.diagnostics_this.paper_trade_count.value is None
    assert payload.diagnostics_this.paper_trade_count.observations == 0


def test_build_payload_engine_errors_none_when_no_ticks(db_session):
    """L22: zero tick events → value=None (NOT 0)."""
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    assert payload.diagnostics_this.engine_invariant_errors.value is None
    assert payload.diagnostics_this.engine_invariant_errors.observations == 0


def test_build_payload_engine_errors_observations(db_session):
    base = datetime(2026, 8, 10, tzinfo=UTC)
    for i in range(15):
        _seed_audit(db_session, ts=base + timedelta(hours=i),
                    event_type="TICK_COMPLETED")
    for i in range(5):
        _seed_audit(db_session, ts=base + timedelta(days=1, hours=i),
                    event_type="ENGINE_INVARIANT_ERROR", reason="r")
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    diag = payload.diagnostics_this.engine_invariant_errors
    assert diag.value == 5             # count of ENGINE_INVARIANT_ERROR
    assert diag.observations == 20     # L6: TICK_COMPLETED + ENGINE_INVARIANT_ERROR


def test_build_payload_engine_errors_reasons_only_from_engine(db_session):
    base = datetime(2026, 8, 10, tzinfo=UTC)
    _seed_audit(db_session, ts=base,
                event_type="ORDER_REJECTED", reason="should_not_appear")
    _seed_audit(db_session, ts=base + timedelta(hours=1),
                event_type="ENGINE_INVARIANT_ERROR", reason="real_engine_reason")
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    diag = payload.diagnostics_this.engine_invariant_errors
    assert tuple(r.reason for r in diag.top_reasons) == ("real_engine_reason",)


def test_build_payload_top_reasons_empty_normalized(db_session):
    """L19: empty `reason` → '(no reason)' bucket.

    The `paper_audit_event.reason` column is `Mapped[str]` with
    `nullable=False, default=""`. NULL is impossible at the schema level,
    so the spec's "NULL or empty" reduces in practice to "empty".
    """
    base = datetime(2026, 8, 10, tzinfo=UTC)
    for i in range(3):
        _seed_audit(
            db_session, ts=base + timedelta(hours=i),
            event_type="ENGINE_INVARIANT_ERROR", reason="",
        )
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    diag = payload.diagnostics_this.engine_invariant_errors
    assert tuple((r.reason, r.count) for r in diag.top_reasons) == (
        ("(no reason)", 3),
    )


def test_build_payload_manifest_none(db_session):
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    op = payload.operational_floor
    assert op.manifest_available is False
    assert op.backup_status == "missing"
    assert op.backup_is_stale is True
    assert op.backup_last_at is None
    assert op.backup_error is None


def test_build_payload_manifest_ok(db_session):
    manifest = {
        "status": "ok",
        "is_stale": False,
        "last_backup_at": "2026-08-17T09:00:00+00:00",
        "error": None,
    }
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=manifest,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    op = payload.operational_floor
    assert op.manifest_available is True
    assert op.backup_status == "ok"
    assert op.backup_is_stale is False
    assert op.backup_last_at == "2026-08-17T09:00:00+00:00"
