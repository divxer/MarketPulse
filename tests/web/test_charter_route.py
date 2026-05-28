"""PR2 — /lab/charter-metrics route tests."""
from __future__ import annotations

import json
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
