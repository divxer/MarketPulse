from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def _login(client, monkeypatch):
    from marketpulse.auth.password import hash_password
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_health_scheduler_never_ran(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    res = client.get("/health/scheduler")
    assert res.status_code == 200
    assert res.json() == {"status": "never_ran", "last_run": None}


def test_health_scheduler_returns_summary(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)

    # Seed a run summary directly via the state helper.
    from marketpulse.db import base as db_base
    from marketpulse.scheduler.state import record_run_summary

    gen = db_base.session_scope()
    s = next(gen)
    record_run_summary(s, {
        "ran_at": "2026-05-11T21:00:00+00:00",
        "finished_at": "2026-05-11T21:00:15+00:00",
        "tickers": [
            {"ticker": "TQQQ", "source": "tencent",
             "splits_added": 0, "dividends_added": 14, "error": None},
        ],
        "total_splits": 0,
        "total_dividends": 14,
        "total_failures": 0,
    })

    res = client.get("/health/scheduler")
    assert res.status_code == 200
    body = res.json()
    assert body["total_dividends"] == 14
    assert body["tickers"][0]["ticker"] == "TQQQ"
    assert body["tickers"][0]["source"] == "tencent"


def test_health_scheduler_requires_auth(client: TestClient) -> None:
    res = client.get("/health/scheduler", follow_redirects=False)
    assert res.status_code in (303, 401)
