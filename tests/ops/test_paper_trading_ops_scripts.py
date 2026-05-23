"""Phase 6h ops hardening script tests."""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_ops_common_resolves_db_url_from_arg_or_env(monkeypatch):
    from scripts._paper_ops_common import resolve_db_url

    monkeypatch.delenv("MARKETPULSE_DB_URL", raising=False)
    assert resolve_db_url("sqlite:///explicit.db") == "sqlite:///explicit.db"
    assert resolve_db_url(None) == "sqlite:///./data/marketpulse.db"

    monkeypatch.setenv("MARKETPULSE_DB_URL", "sqlite:///env.db")
    assert resolve_db_url(None) == "sqlite:///env.db"


def test_ops_common_counts_paper_tables(tmp_path):
    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperCashLedger
    from scripts._paper_ops_common import count_paper_tables

    engine = create_engine(f"sqlite:///{tmp_path / 'ops.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            PaperCashLedger(
                timestamp=datetime(2026, 5, 23, tzinfo=UTC),
                delta=100,
                reason="INITIAL_DEPOSIT",
                balance_after=100,
            ),
        )
        session.commit()

        counts = count_paper_tables(session)

    assert counts["paper_order"] == 0
    assert counts["paper_fill"] == 0
    assert counts["paper_position"] == 0
    assert counts["paper_cash_ledger"] == 1
    assert counts["paper_audit_event"] == 0


def test_health_cli_fresh_db_is_healthy(tmp_path, capsys):
    from marketpulse.db.base import Base
    from scripts.check_paper_trading_health import main

    db_path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    code = main([f"sqlite:///{db_path}", "--skip-price-smoke"])

    out = capsys.readouterr().out
    assert code == 0
    assert "System Status: Healthy" in out
    assert "No paper tick has completed yet" in out


def test_health_cli_attention_for_price_unavailable_three_plus(tmp_path, capsys):
    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperAuditEvent
    from scripts.check_paper_trading_health import main

    db_path = tmp_path / "attention.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        start = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
        session.add(
            PaperAuditEvent(
                timestamp=start,
                event_type="TICK_COMPLETED",
                reason="",
                context={"tick_date": "2026-05-23", "status": "completed"},
            ),
        )
        session.add(
            PaperAuditEvent(
                timestamp=start + timedelta(minutes=1),
                event_type="PRICE_UNAVAILABLE",
                reason="no_price",
                context={"position_id": 7, "ticker": "AAPL", "attempt_count": 3},
            ),
        )
        session.commit()

    code = main([f"sqlite:///{db_path}", "--skip-price-smoke"])

    out = capsys.readouterr().out
    assert code == 1
    assert "System Status: Attention" in out
    assert "PRICE_UNAVAILABLE" in out
    assert "AAPL" in out


def test_health_cli_db_failure_returns_2(capsys):
    from scripts.check_paper_trading_health import main

    code = main(["sqlite:////definitely/missing/path/marketpulse.db"])

    out = capsys.readouterr().out
    assert code == 2
    assert "FAILED:" in out


def test_route_smoke_requires_password(capsys):
    from scripts.smoke_paper_trading_ops import main

    code = main(["--base-url", "http://example.test"])

    out = capsys.readouterr().out
    assert code == 2
    assert "password" in out.lower()


def test_route_smoke_success_with_mock_transport(monkeypatch, capsys):
    import httpx
    from scripts import smoke_paper_trading_ops as smoke

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/lab/paper-trading":
            if "session=" not in request.headers.get("cookie", ""):
                return httpx.Response(303, headers={"location": "/login"})
            return httpx.Response(
                200,
                text=(
                    "Paper Trading · Operations System Status Generated at "
                    "Critical Events Positions Orders & Fills Audit Timeline"
                ),
            )
        if request.method == "POST" and request.url.path == "/login":
            return httpx.Response(303, headers={"Set-Cookie": "session=abc; Path=/"})
        if request.method == "POST" and request.url.path == "/lab/paper-trading":
            return httpx.Response(405)
        return httpx.Response(404)

    monkeypatch.setattr(
        smoke,
        "_client",
        lambda timeout: httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            timeout=timeout,
        ),
    )

    code = smoke.main(["--base-url", "http://example.test", "--password", "dev"])

    out = capsys.readouterr().out
    assert code == 0
    assert "OK: /lab/paper-trading smoke passed" in out


def test_notification_smoke_default_does_not_send(monkeypatch, capsys):
    from scripts import smoke_notifications

    sent = []

    class CapturingNotifier:
        def send(self, title, body, url=None):
            sent.append((title, body, url))
            return True

    monkeypatch.setattr(
        smoke_notifications,
        "get_notifier_from_settings",
        lambda settings: CapturingNotifier(),
    )

    code = smoke_notifications.main([])

    out = capsys.readouterr().out
    assert code == 0
    assert "config OK" in out
    assert sent == []


def test_notification_smoke_send_requires_confirm(capsys):
    from scripts.smoke_notifications import main

    code = main(["--send"])

    out = capsys.readouterr().out
    assert code == 2
    assert "--confirm-send" in out


def test_notification_smoke_send_uses_fixed_smoke_title(monkeypatch):
    from scripts import smoke_notifications

    sent = []

    class CapturingNotifier:
        def send(self, title, body, url=None):
            sent.append((title, body, url))
            return True

    monkeypatch.setattr(
        smoke_notifications,
        "get_notifier_from_settings",
        lambda settings: CapturingNotifier(),
    )

    code = smoke_notifications.main(["--send", "--confirm-send"])

    assert code == 0
    assert sent
    assert sent[0][0].startswith("SMOKE TEST — Paper Trading Notifications")
    assert "SMOKE TEST" in sent[0][1]


def test_health_cli_does_not_mutate_paper_tables(tmp_path):
    from marketpulse.db.base import Base
    from scripts._paper_ops_common import count_paper_tables
    from scripts.check_paper_trading_health import main

    db_path = tmp_path / "nomutate.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        before = count_paper_tables(session)

    code = main([f"sqlite:///{db_path}", "--skip-price-smoke"])

    with Session(engine) as session:
        after = count_paper_tables(session)
    assert code == 0
    assert after == before


def test_notification_smoke_default_does_not_mutate_paper_tables(
    tmp_path,
    monkeypatch,
):
    from marketpulse.db.base import Base
    from scripts._paper_ops_common import count_paper_tables
    from scripts.smoke_notifications import main

    db_path = tmp_path / "notify_nomutate.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    monkeypatch.setenv("MARKETPULSE_DB_URL", f"sqlite:///{db_path}")
    with Session(engine) as session:
        before = count_paper_tables(session)

    code = main([])

    with Session(engine) as session:
        after = count_paper_tables(session)
    assert code == 0
    assert after == before
