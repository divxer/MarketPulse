# Layer: stateful
"""6b+T6: Repository.count_price_unavailable_attempts tests.

Lock 6b+L9: wrapper-only API. External code must not write json_extract
inline. attempt_count progression (1, 2, 3) on consecutive failures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'pu.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_count_zero_when_no_audits(session):
    from marketpulse.trading.repository import Repository
    repo = Repository(session=session)
    assert repo.count_price_unavailable_attempts(position_id=42) == 0


def test_count_returns_audit_rows_with_matching_position_id(session):
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository

    ts = datetime.now(UTC)
    # Three PRICE_UNAVAILABLE audits for position 42; one for position 99.
    for _ in range(3):
        session.add(PaperAuditEvent(
            timestamp=ts, event_type="PRICE_UNAVAILABLE",
            order_id=100, strategy="test", reason="no_data",
            context={"position_id": 42, "ticker": "AAPL"},
        ))
    session.add(PaperAuditEvent(
        timestamp=ts, event_type="PRICE_UNAVAILABLE",
        order_id=200, strategy="test", reason="no_data",
        context={"position_id": 99, "ticker": "MSFT"},
    ))
    # Plus an unrelated event_type — must NOT be counted
    session.add(PaperAuditEvent(
        timestamp=ts, event_type="POSITION_CLOSED",
        order_id=100, strategy="test", reason="",
        context={"position_id": 42, "exit_price": "150"},
    ))
    session.flush()

    repo = Repository(session=session)
    assert repo.count_price_unavailable_attempts(position_id=42) == 3
    assert repo.count_price_unavailable_attempts(position_id=99) == 1
    assert repo.count_price_unavailable_attempts(position_id=999) == 0


def test_count_progression_supports_attempt_count_calculation(session):
    """Lock 6b+L9: consecutive PRICE_UNAVAILABLE audits yield 1, 2, 3
    when each writes `attempt_count = previous_count + 1`."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    ts = datetime.now(UTC)
    pid = 7

    expected_counts: list[int] = []
    for _ in range(3):
        prior = repo.count_price_unavailable_attempts(position_id=pid)
        expected_counts.append(prior + 1)
        session.add(PaperAuditEvent(
            timestamp=ts, event_type="PRICE_UNAVAILABLE",
            order_id=100, strategy="test", reason="no_data",
            context={"position_id": pid, "attempt_count": prior + 1},
        ))
        session.flush()

    assert expected_counts == [1, 2, 3]
    assert repo.count_price_unavailable_attempts(position_id=pid) == 3
