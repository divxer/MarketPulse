"""PR2 — /lab/charter-metrics route tests."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.engine.url import make_url

from marketpulse.auth.password import hash_password


def _login(client: TestClient, monkeypatch) -> None:
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_endpoint_requires_auth(client: TestClient):
    r = client.get("/lab/charter-metrics", headers={"Accept": "application/json"})
    # Unauthenticated → 401; app redirects HTML requests but returns JSON 401
    # when the client signals it accepts JSON.
    assert r.status_code == 401


def test_endpoint_returns_200_with_no_backup_dir(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    body = r.json()
    backup = body["operational_floor"]["backup"]
    # Fresh test DB has no /data/backups dir → status=missing
    assert backup["status"] == "missing"
    assert backup["is_stale"] is True


def _seed_failed_manifest(db_path: Path) -> Path:
    """Write a failed-status manifest at <dbpath>.parent/backups/latest.json."""
    backups_dir = db_path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "failed",
        "timestamp": "2026-05-28T09:00:00+00:00",
        "source": str(db_path),
        "destination": None,
        "size_bytes": None,
        "integrity_check": "not_run",
        "duration_ms": 42,
        "error": "OSError: disk full",
    }
    manifest_path = backups_dir / "latest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return manifest_path


def test_endpoint_returns_200_when_backup_failed(
    client: TestClient, monkeypatch, db_url: str,
):
    _login(client, monkeypatch)
    # Ensure the route sees the same db path that the test fixture uses.
    monkeypatch.setenv("DATABASE_URL", db_url)
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    # Derive db path the same way the route does, via SQLAlchemy's URL parser,
    # so the test matches production behavior across sqlite:/// and
    # sqlite+pysqlite:/// URL forms.
    db_path = Path(make_url(db_url).database).resolve()
    _seed_failed_manifest(db_path)

    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    backup = r.json()["operational_floor"]["backup"]
    assert backup["status"] == "failed"
    assert backup["error"] == "OSError: disk full"


def test_endpoint_non_sqlite_url(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    # Force the settings to look non-SQLite for this request.
    from marketpulse.config import get_settings
    real_settings = get_settings()

    class _StubSettings:
        database_url = "postgresql://user:pw@localhost:5432/mp"
        # Forward any other attribute reads to the real settings.
        def __getattr__(self, name):
            return getattr(real_settings, name)

    monkeypatch.setattr(
        "marketpulse.web.routes.charter.get_settings",
        lambda: _StubSettings(),
    )

    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    backup = r.json()["operational_floor"]["backup"]
    assert backup["status"] == "missing"
    assert "sqlite" in backup["error"].lower()


def test_endpoint_content_type_json(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    # And the body is parseable JSON
    body = r.json()
    assert body["schema_version"] == 1


def _seed_snapshot(session, d: date, *, value: str = "0.025"):
    from marketpulse.portfolio.north_star import NavSnapshot
    from marketpulse.portfolio.snapshot_repo import insert_snapshot
    insert_snapshot(session, NavSnapshot(
        trading_date=d,
        cash_balance=Decimal("100000"),
        holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal("100000"),
        anchor_portfolio_nav=Decimal("100000"),
        portfolio_index=Decimal("1.025"),
        spy_close=Decimal("500"),
        anchor_spy_close=Decimal("500"),
        spy_index=Decimal("1.000"),
        excess_return=Decimal(value),
        trading_days_observed=12,
        coverage_ratio=Decimal("0.133"),
        is_sufficient=False,
        unpriced_positions_count=0,
        unpriced_tickers=(),
    ))


def test_endpoint_north_star_empty(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    ns = r.json()["north_star"]
    assert ns["error"] == "no_snapshots_yet"
    assert ns["value"] is None


def test_endpoint_north_star_with_snapshot(client, monkeypatch, db_url):
    """Snapshot is seeded via a fresh session against the test DB URL, then
    the endpoint reads it through the FastAPI-managed session."""
    _login(client, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", db_url)
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    engine = create_engine(db_url)
    with Session(engine) as s:
        _seed_snapshot(s, date(2026, 8, 14))
        s.commit()

    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    ns = r.json()["north_star"]
    assert ns["error"] is None
    assert ns["value"] == 0.025


def test_endpoint_diagnostics_populated(client, monkeypatch, db_url):
    _login(client, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", db_url)
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from marketpulse.db.models import PaperAuditEvent

    engine = create_engine(db_url)
    with Session(engine) as s:
        # Seed 30 snapshots over 30 days for window establishment.
        for i in range(30):
            _seed_snapshot(s, date(2026, 7, 15) + timedelta(days=i))
        base = datetime(2026, 7, 15, tzinfo=UTC)
        for i in range(15):
            s.add(PaperAuditEvent(
                timestamp=base + timedelta(days=i),
                event_type="TICK_COMPLETED",
                order_id=None, strategy=None, reason="", context={},
            ))
        s.commit()

    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    diag = r.json()["diagnostics"]["tick_success_rate_30d"]
    assert diag["value"] == 1.0
    assert diag["observations"] == 15
    assert "coverage_ratio" in diag


def test_endpoint_decimals_serialized_as_floats(client, monkeypatch, db_url):
    """L17: response numeric fields are JSON numbers (float), not Decimal strings."""
    _login(client, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", db_url)
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    engine = create_engine(db_url)
    with Session(engine) as s:
        _seed_snapshot(s, date(2026, 8, 14), value="0.04")
        s.commit()

    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    ns = r.json()["north_star"]
    assert type(ns["value"]) is float
    assert type(ns["portfolio_index"]) is float
    assert type(ns["spy_index"]) is float
    assert type(ns["coverage_ratio"]) is float


def test_endpoint_no_network_call(client, monkeypatch, db_url):
    """Read path is DB-only — yfinance must never be touched."""
    _login(client, monkeypatch)
    import marketpulse.data.yfinance_client as yf_mod

    def boom(*a, **kw):  # noqa: ANN001
        raise RuntimeError("yfinance must not be called from endpoint")

    monkeypatch.setattr(yf_mod.YFinanceClient, "__init__", boom, raising=False)

    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
