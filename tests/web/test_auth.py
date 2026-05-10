import pytest
from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password


def test_unauthenticated_redirected(client: TestClient) -> None:
    res = client.get("/", follow_redirects=False)
    assert res.status_code in (302, 303, 307)
    assert res.headers["location"].endswith("/login")


def test_login_success_sets_cookie(client: TestClient, monkeypatch) -> None:
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    res = client.post("/login", data={"password": pw}, follow_redirects=False)
    assert res.status_code in (302, 303)
    assert "mp_session" in res.cookies


def test_login_failure(client: TestClient) -> None:
    res = client.post("/login", data={"password": "wrong"})
    assert res.status_code == 401
