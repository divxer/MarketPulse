from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_post_splits_creates_row(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/splits", data={
        "ticker": "TQQQ",
        "ex_date": "2025-11-20",
        "ratio": 2.0,
        "notes": "test split",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["ticker"] == "TQQQ"
    assert body["ex_date"] == "2025-11-20"
    assert body["ratio"] == 2.0
    assert body["source"] == "manual"


def test_post_splits_rejects_bad_ratio(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    for bad in ("0", "1", "-1"):
        res = client.post("/splits", data={
            "ticker": "X", "ex_date": "2025-01-01", "ratio": bad,
        })
        assert res.status_code == 422, f"ratio={bad!r} should be rejected"


def test_post_splits_rejects_bad_date(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/splits", data={
        "ticker": "X", "ex_date": "not-a-date", "ratio": 2.0,
    })
    assert res.status_code == 422


def test_post_splits_duplicate_rejected(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    client.post("/splits", data={"ticker": "X", "ex_date": "2025-01-01", "ratio": 2})
    res = client.post("/splits", data={"ticker": "X", "ex_date": "2025-01-01", "ratio": 3})
    assert res.status_code == 422
    assert "already recorded" in res.json()["detail"]


def test_get_splits_filters_by_ticker(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    client.post("/splits", data={"ticker": "TQQQ", "ex_date": "2025-11-20", "ratio": 2})
    client.post("/splits", data={"ticker": "NVDA", "ex_date": "2024-06-10", "ratio": 10})

    res = client.get("/splits")
    assert res.status_code == 200
    assert len(res.json()) == 2

    res = client.get("/splits?ticker=TQQQ")
    assert len(res.json()) == 1
    assert res.json()[0]["ticker"] == "TQQQ"


def test_delete_splits_recomputes_holding(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    # 1) Buy 20 shares @ $30
    client.post("/trades", data={
        "ticker": "X", "action": "buy", "quantity": 20, "price": 30,
        "fees": 0, "executed_at": "2024-01-15",
    })
    # 2) Record 1:2 split → holding becomes 40 @ $15
    create = client.post("/splits", data={
        "ticker": "X", "ex_date": "2025-06-01", "ratio": 2,
    })
    split_id = create.json()["id"]

    # The POST /splits handler must trigger recompute. Verify via /holdings.
    res = client.get("/holdings")
    assert "X" in res.text
    assert "40" in res.text

    # 3) Delete split → recompute → 20 @ $30
    res = client.delete(f"/splits/{split_id}")
    assert res.status_code == 200
    res = client.get("/holdings")
    assert ">20<" in res.text or ">20.00<" in res.text or ">20 <" in res.text


def test_post_splits_requires_auth(client: TestClient):
    res = client.post(
        "/splits",
        data={"ticker": "X", "ex_date": "2025-01-01", "ratio": 2},
        follow_redirects=False,
    )
    # Unauthenticated requests redirect to /login (HTML) or 401 (JSON).
    assert res.status_code in (303, 401)
