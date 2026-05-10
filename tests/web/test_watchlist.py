from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password


def _login(client: TestClient, monkeypatch) -> None:
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_add_and_list_watchlist(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    res = client.post("/watchlist", data={"ticker": "AAPL"})
    assert res.status_code == 200
    assert "AAPL" in res.text
    page = client.get("/watchlist")
    assert "AAPL" in page.text


def test_delete_watchlist_item(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    client.post("/watchlist", data={"ticker": "TSLA"})
    from sqlalchemy import text

    from marketpulse.db.base import get_engine
    with get_engine().connect() as conn:
        row_id = conn.execute(
            text("SELECT id FROM watchlist_items WHERE ticker='TSLA'")
        ).scalar_one()
    res = client.delete(f"/watchlist/{row_id}")
    assert res.status_code == 200
    assert "TSLA" not in client.get("/watchlist").text


def test_invalid_ticker_rejected(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    res = client.post("/watchlist", data={"ticker": "  "})
    assert res.status_code == 422
