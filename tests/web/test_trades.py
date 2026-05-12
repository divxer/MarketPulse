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
    assert "暂无记录" in res.text


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


def test_delete_trade_recomputes_holding(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    # Buy 100 @ 10, buy 100 @ 20 → avg 15. Sell 50 @ 25 → realized +500.
    client.post("/trades", data={
        "ticker": "ZZZ", "action": "buy", "quantity": 100, "price": 10,
    })
    r = client.post("/trades", data={
        "ticker": "ZZZ", "action": "buy", "quantity": 100, "price": 20,
    })
    # Get second buy's id from the response (its row id appears in trade-row-N).
    import re as _re
    ids = sorted(int(m) for m in _re.findall(r'id="trade-row-(\d+)"', r.text))
    second_buy_id = ids[-1]
    client.post("/trades", data={"ticker": "ZZZ", "action": "sell", "quantity": 50, "price": 25})

    # Delete the second buy (the @20 one). Remaining: buy 100@10, sell 50@25.
    # avg_cost should drop to 10, sell's realized_pl should become (25-10)*50 = 750.
    r = client.delete(f"/trades/{second_buy_id}")
    assert r.status_code == 200

    r = client.get("/trades?ticker=ZZZ")
    assert "+750.00" in r.text
    r = client.get("/holdings")
    assert "ZZZ" in r.text  # 50 shares remain


def test_delete_nonexistent_trade_404(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.delete("/trades/99999")
    assert r.status_code == 404


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


def test_trades_timeline_shows_splits_and_dividends(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    # Trade
    client.post("/trades", data={
        "ticker": "TQQQ", "action": "buy", "quantity": 20, "price": 30,
        "fees": 0, "executed_at": "2024-01-15",
    })
    # Split
    client.post("/splits", data={
        "ticker": "TQQQ", "ex_date": "2025-11-20", "ratio": 2,
    })
    # Dividend
    client.post("/dividends", data={
        "ticker": "TQQQ", "ex_date": "2025-09-24",
        "amount_per_share": 0.10, "total_amount": 4.0,
    })

    res = client.get("/trades")
    assert res.status_code == 200
    # All three event types render
    assert "买入" in res.text
    assert "拆股" in res.text or "1 → 2" in res.text
    assert "分红" in res.text


def test_trades_timeline_filter_splits_only(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    client.post("/trades", data={
        "ticker": "X", "action": "buy", "quantity": 10, "price": 100,
        "fees": 0, "executed_at": "2024-01-15",
    })
    client.post("/splits", data={
        "ticker": "X", "ex_date": "2025-01-01", "ratio": 2,
    })

    res = client.get("/trades?event_type=split")
    assert res.status_code == 200
    assert "拆股" in res.text or "1 → 2" in res.text
    # The buy row should not appear in split-only view (filter by table rows, not form options)
    assert "trade-row-" not in res.text


def test_trade_form_includes_executed_at_input(client: TestClient, monkeypatch):
    """Regression: the unified /trades form must include an executed_at
    date input for backfilling historical trades. Without it, manually-entered
    trades can only be dated 'today'."""
    _login(client, monkeypatch)
    res = client.get("/trades")
    assert res.status_code == 200
    # The input must be in the trade-field group (visible when 买入/卖出
    # is selected, hidden for splits/dividends).
    assert 'name="executed_at"' in res.text
    assert 'trade-field' in res.text
