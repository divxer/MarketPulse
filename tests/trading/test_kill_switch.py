# Layer: behavioral
"""6a-2.4 KillSwitchState — env + DB flag + audit row."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'ks.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_kill_switch_env_var_force_on(monkeypatch, session):
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository

    monkeypatch.setenv("MP_PAPER_KILL_SWITCH", "1")
    repo = Repository(session=session)
    ks = KillSwitchState(env_var="MP_PAPER_KILL_SWITCH", repository=repo)
    assert ks.is_active() is True


def test_kill_switch_db_flag(monkeypatch, session):
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository

    monkeypatch.delenv("MP_PAPER_KILL_SWITCH", raising=False)
    repo = Repository(session=session)
    ks = KillSwitchState(env_var="MP_PAPER_KILL_SWITCH", repository=repo)
    assert ks.is_active() is False

    ks.flip(
        new_state=True,
        reason="manual",
        actor="test",
        timestamp=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
    )
    # New repo instance to confirm DB persisted
    repo2 = Repository(session=session)
    ks2 = KillSwitchState(env_var="MP_PAPER_KILL_SWITCH", repository=repo2)
    assert ks2.is_active() is True


def test_kill_switch_flip_writes_audit(monkeypatch, session):
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository

    monkeypatch.delenv("MP_PAPER_KILL_SWITCH", raising=False)
    repo = Repository(session=session)
    ks = KillSwitchState(env_var="MP_PAPER_KILL_SWITCH", repository=repo)
    ks.flip(
        new_state=True, reason="manual", actor="alice",
        timestamp=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
    )
    rows = session.execute(select(PaperAuditEvent)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "KILL_SWITCH_FLIPPED"
    assert rows[0].context["actor"] == "alice"
    assert rows[0].context["to_state"] is True
