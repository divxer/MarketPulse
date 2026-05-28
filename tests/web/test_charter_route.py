"""PR2 — /lab/charter-metrics route tests."""
from __future__ import annotations

from fastapi.testclient import TestClient

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
