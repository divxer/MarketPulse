# Layer: stateful
"""6g-T8: operator-triggered notification replay CLI."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketpulse.db.base import Base
from marketpulse.db.models import PaperAuditEvent


class CapturingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []

    def send(self, title, body, url=None) -> bool:
        self.sent.append((title, body, url))
        return True


class FailingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []

    def send(self, title, body, url=None) -> bool:
        self.sent.append((title, body, url))
        return False


@pytest.fixture
def patched_db(tmp_path, monkeypatch):
    from marketpulse.config import get_settings
    from marketpulse.db import base as base_mod

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    get_settings.cache_clear()

    engine = create_engine(f"sqlite:///{tmp_path / 'rp.db'}")
    Base.metadata.create_all(engine)

    def scope():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(base_mod, "session_scope", scope)
    yield engine
    get_settings.cache_clear()


def _seed_tick(session: Session) -> None:
    timestamp = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    session.add(
        PaperAuditEvent(
            timestamp=timestamp,
            event_type="TICK_COMPLETED",
            order_id=None,
            strategy=None,
            reason="",
            context={"tick_date": "2026-05-22", "status": "completed"},
        )
    )


def test_republish_cli_refuses_when_disabled(patched_db, monkeypatch, capsys):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "false")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.republish_cli import main

    exit_code = main(["--date", "2026-05-22"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "MP_PAPER_NOTIFICATIONS_ENABLED" in captured.err


def test_republish_cli_runs_with_provided_notifier(
    patched_db,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    timestamp = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    with Session(patched_db) as session:
        session.add(
            PaperAuditEvent(
                timestamp=timestamp,
                event_type="ORDER_ENTRY_FILLED",
                order_id=1,
                strategy="momentum",
                reason="",
                context={"ticker": "AAPL", "fill_price": "155.500000"},
            )
        )
        _seed_tick(session)
        session.commit()

    notifier = CapturingNotifier()
    from marketpulse.observability import republish_cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "get_notifier_from_settings",
        lambda settings: notifier,
    )

    exit_code = cli_module.main(["--date", "2026-05-22"])

    assert exit_code == 0
    assert any(title.startswith("📊 Paper Tick") for title, _, _ in notifier.sent)
    captured = capsys.readouterr()
    assert "summary_title: 📊 Paper Tick" in captured.out
    assert "failures: (none)" in captured.out


def test_republish_cli_rejects_invalid_date(patched_db, monkeypatch):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from marketpulse.observability.republish_cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--date", "not-a-date"])

    assert excinfo.value.code != 0


def test_republish_cli_failure_exit_code(patched_db, monkeypatch, capsys):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    timestamp = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    with Session(patched_db) as session:
        session.add(
            PaperAuditEvent(
                timestamp=timestamp,
                event_type="ORDER_PLACED",
                order_id=1,
                strategy="momentum",
                reason="",
                context={"ticker": "AAPL", "quantity": 10},
            )
        )
        _seed_tick(session)
        session.commit()

    from marketpulse.observability import republish_cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "get_notifier_from_settings",
        lambda settings: FailingNotifier(),
    )

    exit_code = cli_module.main(["--date", "2026-05-22"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "send_returned_false" in captured.out
