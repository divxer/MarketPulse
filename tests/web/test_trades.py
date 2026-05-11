from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_trades_page_empty(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.get("/trades")
    assert res.status_code == 200
    assert "暂无交易记录" in res.text


def test_add_buy_and_sell_trades(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/trades", data={
        "ticker": "NVDA", "action": "buy", "quantity": 10, "price": 200,
        "fees": 0, "notes": "initial",
    })
    assert res.status_code == 200
    assert "NVDA" in res.text

    res = client.post("/trades", data={
        "ticker": "NVDA", "action": "sell", "quantity": 4, "price": 300,
        "fees": 0, "notes": "partial",
    })
    assert res.status_code == 200
    # realized_pl = (300-200)*4 = 400
    assert "+400.00" in res.text


def test_oversell_via_route_returns_422(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    client.post("/trades", data={"ticker": "X", "action": "buy", "quantity": 5, "price": 100})
    res = client.post("/trades", data={
        "ticker": "X", "action": "sell", "quantity": 10, "price": 110,
    })
    assert res.status_code == 422


def test_holdings_page_shows_realized_pl(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    # Make a profitable round-trip on TSLA so realized P&L is non-zero
    client.post("/trades", data={"ticker": "TSLA", "action": "buy", "quantity": 10, "price": 100})
    client.post("/trades", data={"ticker": "TSLA", "action": "sell", "quantity": 10, "price": 150})
    res = client.get("/holdings")
    assert res.status_code == 200
    assert "已实现盈亏" in res.text
    assert "+500.00" in res.text  # (150-100)*10
