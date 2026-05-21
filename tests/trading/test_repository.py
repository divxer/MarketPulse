# Layer: invariant
"""6a-2: repository single-writer surface basics."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    db_file = tmp_path / "repo.db"
    eng = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_write_audit_event_appends_row(session):
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.types import AuditEventType

    repo = Repository(session=session)
    repo.write_audit_event(
        event_type=AuditEventType.KILL_SWITCH_FLIPPED,
        order_id=None,
        strategy=None,
        reason="test",
        context={"k": "v"},
        timestamp=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
    )
    rows = session.execute(select(PaperAuditEvent)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "KILL_SWITCH_FLIPPED"
    assert rows[0].context == {"k": "v"}


def test_write_duplicate_audit_once_dedupes(session):
    """6a-L5: ORDER_PLACED_DUPLICATE deduped per (idempotency_key, tick_date)."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    args = dict(
        idempotency_key="abc123",
        order_id=42,
        strategy="s",
        tick_date=date(2026, 5, 21),
        context={"allocation_run_id": "paper-2026-05-21"},
        timestamp=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
    )
    repo.write_duplicate_audit_once(**args)
    repo.write_duplicate_audit_once(**args)  # second call no-op
    rows = session.execute(select(PaperAuditEvent)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "ORDER_PLACED_DUPLICATE"


def test_write_gap_audit_once_dedupes(session):
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    args = dict(
        last_tick=date(2026, 5, 18),
        resume_date=date(2026, 5, 21),
        missed_business_days=2,
        timestamp=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
    )
    repo.write_gap_audit_once(**args)
    repo.write_gap_audit_once(**args)
    rows = session.execute(select(PaperAuditEvent)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "SCHEDULER_GAP_DETECTED"


def test_write_tick_completed_once_no_op_when_terminal_completed(session):
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    ctx_done = {"tick_date": "2026-05-21", "status": "completed"}
    ts = datetime(2026, 5, 21, 17, 30, tzinfo=UTC)
    repo.write_tick_completed_once(tick_date=date(2026, 5, 21), context=ctx_done, timestamp=ts)
    # Second call with same status is no-op
    repo.write_tick_completed_once(tick_date=date(2026, 5, 21), context=ctx_done, timestamp=ts)
    rows = session.execute(select(PaperAuditEvent)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "TICK_COMPLETED"


def test_write_tick_completed_once_appends_reprocessed_on_recovery(session):
    """6a-L5 / 6a-L8: completed_with_errors followed by completed appends
    TICK_REPROCESSED_COMPLETED; original row is preserved."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    ts = datetime(2026, 5, 21, 17, 30, tzinfo=UTC)
    repo.write_tick_completed_once(
        tick_date=date(2026, 5, 21),
        context={"tick_date": "2026-05-21", "status": "completed_with_errors"},
        timestamp=ts,
    )
    repo.write_tick_completed_once(
        tick_date=date(2026, 5, 21),
        context={"tick_date": "2026-05-21", "status": "completed"},
        timestamp=ts,
    )
    rows = (
        session.execute(select(PaperAuditEvent).order_by(PaperAuditEvent.id))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert rows[0].event_type == "TICK_COMPLETED"
    assert rows[0].context["status"] == "completed_with_errors"
    assert rows[1].event_type == "TICK_REPROCESSED_COMPLETED"
    assert rows[1].context["new_status"] == "completed"
