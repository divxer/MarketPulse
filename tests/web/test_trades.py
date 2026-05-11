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


_RH_HEADER = (
    "Activity Date,Process Date,Settle Date,Instrument,Description,"
    "Trans Code,Quantity,Price,Amount\n"
)


def test_robinhood_import_preview_and_confirm(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    csv = _RH_HEADER + (
        "5/8/2026,5/9/2026,5/12/2026,AAPL,Apple,Buy,10,$180.00,($1800.00)\n"
        "5/9/2026,5/10/2026,5/13/2026,AAPL,Apple,Sell,4,$200.00,$800.00\n"
        "5/1/2026,5/2/2026,5/3/2026,AAPL,Dividend,CDIV,,,$5.00\n"
    )
    res = client.post(
        "/trades/import",
        files={"file": ("activity.csv", csv, "text/csv")},
    )
    assert res.status_code == 200
    assert "AAPL" in res.text
    assert "2 笔" in res.text  # 2 new trades

    res = client.post("/trades/import/confirm", data={"csv_text": csv})
    assert res.status_code == 200
    assert "新增" in res.text

    res = client.get("/trades")
    assert "AAPL" in res.text
    # Realized P&L = (200-180)*4 = 80
    assert "+80.00" in res.text


def test_robinhood_import_skips_duplicates(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    csv = _RH_HEADER + "5/8/2026,5/9/2026,5/12/2026,SPY,SPDR,Buy,1,$500.00,($500.00)\n"
    # First import
    client.post("/trades/import/confirm", data={"csv_text": csv})
    # Re-upload same file → preview should show 0 new, 1 skipped
    res = client.post(
        "/trades/import",
        files={"file": ("activity.csv", csv, "text/csv")},
    )
    assert res.status_code == 200
    assert "0 笔为新交易" in res.text or "0</span> 笔为新交易" in res.text


def test_trade_post_accepts_executed_at(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/trades", data={
        "ticker": "QUBT", "action": "buy", "quantity": 100, "price": 19.70,
        "fees": 0, "notes": "historical", "executed_at": "2025-06-17",
    })
    assert res.status_code == 200
    res = client.get("/trades")
    assert "2025-06-17" in res.text or "06-17" in res.text


def test_trade_post_rejects_invalid_executed_at(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/trades", data={
        "ticker": "QUBT", "action": "buy", "quantity": 1, "price": 1,
        "executed_at": "not-a-date",
    })
    assert res.status_code == 422


def test_robinhood_import_bad_csv_returns_422(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post(
        "/trades/import",
        files={"file": ("bad.csv", "no,header,here\n1,2,3\n", "text/csv")},
    )
    assert res.status_code == 422


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
