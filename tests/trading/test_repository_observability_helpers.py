# Layer: stateful
"""6g-T2: Repository observability helpers (locks 6g-L5, L17, L20)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'obs.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session


def _audit(
    session,
    *,
    event_type,
    timestamp,
    context=None,
    strategy=None,
    order_id=None,
    reason="",
):
    """Direct INSERT for read-helper fixtures; production writes stay wrapped."""
    from marketpulse.db.models import PaperAuditEvent

    row = PaperAuditEvent(
        timestamp=timestamp,
        event_type=event_type,
        order_id=order_id,
        strategy=strategy,
        reason=reason,
        context=context or {},
    )
    session.add(row)
    session.flush()
    return row


def test_positions_with_prior_pu_empty_position_ids_returns_empty(session):
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)

    out = repo.positions_with_prior_price_unavailable(
        position_ids=[],
        before=datetime(2026, 5, 22, tzinfo=UTC),
    )

    assert out == set()


def test_positions_with_prior_pu_matches_by_context_position_id(session):
    """Lock 6g-L17: position_id 1 has prior PRICE_UNAVAILABLE; 2 has none."""
    from marketpulse.trading.repository import Repository

    cutoff = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=cutoff - timedelta(days=2),
        context={"position_id": 1, "attempt_count": 1},
    )
    repo = Repository(session=session)

    out = repo.positions_with_prior_price_unavailable(
        position_ids=[1, 2],
        before=cutoff,
    )

    assert out == {1}


def test_positions_with_prior_pu_excludes_concurrent_timestamps(session):
    """Lock 6g-L4b: prior history is strictly before, not concurrent."""
    from marketpulse.trading.repository import Repository

    cutoff = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=cutoff,
        context={"position_id": 1, "attempt_count": 1},
    )
    repo = Repository(session=session)

    out = repo.positions_with_prior_price_unavailable(
        position_ids=[1],
        before=cutoff,
    )

    assert out == set()


def test_positions_with_prior_pu_multi_position(session):
    from marketpulse.trading.repository import Repository

    cutoff = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    earlier = cutoff - timedelta(days=3)
    _audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=earlier,
        context={"position_id": 1, "attempt_count": 1},
    )
    _audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=earlier,
        context={"position_id": 3, "attempt_count": 2},
    )
    repo = Repository(session=session)

    out = repo.positions_with_prior_price_unavailable(
        position_ids=[1, 2, 3, 4],
        before=cutoff,
    )

    assert out == {1, 3}


def test_kill_switch_cycle_skipped_in_period_false_when_no_flipped(session):
    """Lock 6g-L5: orphan skipped row is anomalous, so do not dedup."""
    from marketpulse.trading.repository import Repository

    cutoff = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(
        session,
        event_type="KILL_SWITCH_CYCLE_SKIPPED",
        timestamp=cutoff - timedelta(hours=1),
        context={"tick_date": "2026-05-22"},
    )
    repo = Repository(session=session)

    assert repo.kill_switch_cycle_skipped_in_active_period(before=cutoff) is False


def test_kill_switch_cycle_skipped_in_period_false_when_no_skip_since_flip(
    session,
):
    from marketpulse.trading.repository import Repository

    cutoff = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(
        session,
        event_type="KILL_SWITCH_FLIPPED",
        timestamp=cutoff - timedelta(hours=2),
        context={"to_state": True, "reason": "drawdown"},
    )
    repo = Repository(session=session)

    assert repo.kill_switch_cycle_skipped_in_active_period(before=cutoff) is False


def test_kill_switch_cycle_skipped_in_period_true_when_skip_since_flip(session):
    from marketpulse.trading.repository import Repository

    cutoff = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(
        session,
        event_type="KILL_SWITCH_FLIPPED",
        timestamp=cutoff - timedelta(hours=3),
        context={"to_state": True},
    )
    _audit(
        session,
        event_type="KILL_SWITCH_CYCLE_SKIPPED",
        timestamp=cutoff - timedelta(hours=1),
        context={"tick_date": "2026-05-22"},
    )
    repo = Repository(session=session)

    assert repo.kill_switch_cycle_skipped_in_active_period(before=cutoff) is True


def test_kill_switch_cycle_skipped_in_period_resets_after_clear_and_reflip(
    session,
):
    from marketpulse.trading.repository import Repository

    cutoff = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(
        session,
        event_type="KILL_SWITCH_FLIPPED",
        timestamp=cutoff - timedelta(days=5),
        context={"to_state": True},
    )
    _audit(
        session,
        event_type="KILL_SWITCH_CYCLE_SKIPPED",
        timestamp=cutoff - timedelta(days=4),
        context={"tick_date": "2026-05-17"},
    )
    _audit(
        session,
        event_type="KILL_SWITCH_FLIPPED",
        timestamp=cutoff - timedelta(days=3),
        context={"to_state": False},
    )
    _audit(
        session,
        event_type="KILL_SWITCH_FLIPPED",
        timestamp=cutoff - timedelta(hours=2),
        context={"to_state": True},
    )
    repo = Repository(session=session)

    assert repo.kill_switch_cycle_skipped_in_active_period(before=cutoff) is False


def test_kill_switch_cycle_skipped_in_period_respects_before_cutoff(session):
    from marketpulse.trading.repository import Repository

    cutoff = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(
        session,
        event_type="KILL_SWITCH_FLIPPED",
        timestamp=cutoff - timedelta(hours=3),
        context={"to_state": True},
    )
    _audit(
        session,
        event_type="KILL_SWITCH_CYCLE_SKIPPED",
        timestamp=cutoff,
        context={"tick_date": "2026-05-22"},
    )
    repo = Repository(session=session)

    assert repo.kill_switch_cycle_skipped_in_active_period(before=cutoff) is False


def test_kill_switch_cycle_skipped_in_period_false_after_latest_clear(session):
    """A cleared latest flip means a later skipped row should still emit."""
    from marketpulse.trading.repository import Repository

    cutoff = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(
        session,
        event_type="KILL_SWITCH_FLIPPED",
        timestamp=cutoff - timedelta(days=2),
        context={"to_state": True},
    )
    _audit(
        session,
        event_type="KILL_SWITCH_CYCLE_SKIPPED",
        timestamp=cutoff - timedelta(days=1),
        context={"tick_date": "2026-05-21"},
    )
    _audit(
        session,
        event_type="KILL_SWITCH_FLIPPED",
        timestamp=cutoff - timedelta(hours=2),
        context={"to_state": False},
    )
    repo = Repository(session=session)

    assert repo.kill_switch_cycle_skipped_in_active_period(before=cutoff) is False


def test_latest_price_unavailable_attempt_counts_returns_prior_max(session):
    from marketpulse.trading.repository import Repository

    cutoff = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=cutoff - timedelta(days=3),
        context={"position_id": 1, "attempt_count": 1},
    )
    _audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=cutoff - timedelta(days=2),
        context={"position_id": 1, "attempt_count": 5},
    )
    _audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=cutoff - timedelta(days=1),
        context={"position_id": 2, "attempt_count": 2},
    )
    repo = Repository(session=session)

    out = repo.latest_price_unavailable_attempt_counts(
        position_ids=[1, 2, 3],
        before=cutoff,
    )

    assert out == {1: 5, 2: 2}


def test_latest_price_unavailable_attempt_counts_respects_before_cutoff(session):
    from marketpulse.trading.repository import Repository

    cutoff = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=cutoff - timedelta(days=1),
        context={"position_id": 1, "attempt_count": 2},
    )
    _audit(
        session,
        event_type="PRICE_UNAVAILABLE",
        timestamp=cutoff,
        context={"position_id": 1, "attempt_count": 9},
    )
    repo = Repository(session=session)

    out = repo.latest_price_unavailable_attempt_counts(
        position_ids=[1],
        before=cutoff,
    )

    assert out == {1: 2}


def test_latest_price_unavailable_attempt_counts_empty_ids_returns_empty(session):
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)

    out = repo.latest_price_unavailable_attempt_counts(
        position_ids=[],
        before=datetime(2026, 5, 22, tzinfo=UTC),
    )

    assert out == {}


def test_latest_tick_completed_returns_none_when_empty(session):
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)

    out = repo.latest_tick_completed_timestamp(
        before=datetime(2026, 5, 22, tzinfo=UTC),
    )

    assert out is None


def test_latest_tick_completed_returns_most_recent(session):
    from marketpulse.trading.repository import Repository

    cutoff = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    older = cutoff - timedelta(days=2)
    newer = cutoff - timedelta(days=1)
    _audit(
        session,
        event_type="TICK_COMPLETED",
        timestamp=older,
        context={"tick_date": "2026-05-20"},
    )
    _audit(
        session,
        event_type="TICK_COMPLETED",
        timestamp=newer,
        context={"tick_date": "2026-05-21"},
    )
    repo = Repository(session=session)

    out = repo.latest_tick_completed_timestamp(before=cutoff)

    assert out == newer


def test_latest_tick_completed_respects_before_cutoff(session):
    from marketpulse.trading.repository import Repository

    cutoff = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(
        session,
        event_type="TICK_COMPLETED",
        timestamp=cutoff,
        context={"tick_date": "2026-05-22"},
    )
    older = cutoff - timedelta(days=1)
    _audit(
        session,
        event_type="TICK_COMPLETED",
        timestamp=older,
        context={"tick_date": "2026-05-21"},
    )
    repo = Repository(session=session)

    out = repo.latest_tick_completed_timestamp(before=cutoff)

    assert out == older
