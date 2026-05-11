from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_alerts_page_empty(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.get("/alerts")
    assert res.status_code == 200
    assert "暂无告警规则" in res.text


def test_add_alert_rule(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/alerts", data={
        "ticker": "NVDA", "metric": "price", "op": ">=", "threshold": 250,
        "notes": "突破前高",
    })
    assert res.status_code == 200
    assert "NVDA" in res.text
    assert "突破前高" in res.text
    page = client.get("/alerts")
    assert "NVDA" in page.text


def test_invalid_metric_rejected(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/alerts", data={
        "ticker": "NVDA", "metric": "bogus", "op": ">=", "threshold": 100,
    })
    assert res.status_code == 422


def test_invalid_op_rejected(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/alerts", data={
        "ticker": "NVDA", "metric": "price", "op": "==", "threshold": 100,
    })
    assert res.status_code == 422


def test_toggle_alert(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    client.post("/alerts", data={
        "ticker": "AAPL", "metric": "price", "op": "<=", "threshold": 180,
    })
    from sqlalchemy import text

    from marketpulse.db.base import get_engine
    with get_engine().connect() as conn:
        row_id = conn.execute(text("SELECT id FROM alert_rules WHERE ticker='AAPL'")).scalar_one()
    res = client.post(f"/alerts/{row_id}/toggle")
    assert res.status_code == 200
    assert "禁用" in res.text  # toggled off
    res = client.post(f"/alerts/{row_id}/toggle")
    assert "启用" in res.text  # back on


def test_delete_alert(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    client.post("/alerts", data={
        "ticker": "MSFT", "metric": "price", "op": ">=", "threshold": 400,
    })
    from sqlalchemy import text

    from marketpulse.db.base import get_engine
    with get_engine().connect() as conn:
        row_id = conn.execute(text("SELECT id FROM alert_rules WHERE ticker='MSFT'")).scalar_one()
    res = client.delete(f"/alerts/{row_id}")
    assert res.status_code == 200
    page = client.get("/alerts")
    assert "MSFT" not in page.text
