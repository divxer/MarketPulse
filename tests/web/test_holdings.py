from datetime import UTC, datetime

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.data.types import Fundamentals, Quote


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


class _FakeData:
    def __init__(self, price: float = 250.0):
        self.price = price

    def get_quote(self, ticker):
        return Quote(
            ticker=ticker, price=self.price, change_pct=1.0,
            volume=10, avg_volume_20d=10, fetched_at=datetime.now(UTC),
        )

    def get_history(self, ticker, period="60d"):
        return []

    def get_news(self, ticker, limit=10):
        return []

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, market_cap=1, pe_ratio=10,
                            eps=1, sector="t", industry="i")


def test_holdings_page_empty(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.get("/holdings")
    assert res.status_code == 200
    assert "暂无持仓" in res.text


def test_holdings_built_from_trades(client: TestClient, monkeypatch):
    """Buying via /trades populates the Holdings page."""
    _login(client, monkeypatch)
    fake = _FakeData(price=300.0)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        # buy NVDA: 10 shares @ $200, current $300 → +$1000 (+50%)
        client.post("/trades", data={
            "ticker": "NVDA", "action": "buy", "quantity": 10, "price": 200,
        })
        page = client.get("/holdings")
        assert page.status_code == 200
        assert "NVDA" in page.text
        assert "300.00" in page.text       # current price
        assert "3000.00" in page.text      # market value
        assert "+1000.00" in page.text     # pl dollars
        assert "+50.00" in page.text       # pl pct
    finally:
        client.app.dependency_overrides.clear()


def test_delete_holding_for_cleanup(client: TestClient, monkeypatch):
    """Delete row remains for cleaning up stale data."""
    _login(client, monkeypatch)
    fake = _FakeData()
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        client.post("/trades", data={
            "ticker": "GOOG", "action": "buy", "quantity": 4, "price": 150,
        })
        from sqlalchemy import text

        from marketpulse.db.base import get_engine
        with get_engine().connect() as conn:
            row_id = conn.execute(text("SELECT id FROM holdings WHERE ticker='GOOG'")).scalar_one()
        res = client.delete(f"/holdings/{row_id}")
        assert res.status_code == 200
        assert "GOOG" not in client.get("/holdings").text
    finally:
        client.app.dependency_overrides.clear()


def test_holdings_add_endpoint_removed(client: TestClient, monkeypatch):
    """Direct holdings creation is no longer supported — must go through trades."""
    _login(client, monkeypatch)
    res = client.post("/holdings", data={
        "ticker": "NVDA", "quantity": 10, "avg_cost": 200,
    })
    # 405 Method Not Allowed (route removed)
    assert res.status_code == 405


def test_holdings_update_endpoint_removed(client: TestClient, monkeypatch):
    """Direct edit also removed."""
    _login(client, monkeypatch)
    res = client.post("/holdings/1/update", data={"quantity": 1, "avg_cost": 1})
    assert res.status_code == 404


def test_holdings_resilient_to_quote_failure(client: TestClient, monkeypatch):
    """If yfinance fails for a ticker, the row still renders with cost-basis info."""
    _login(client, monkeypatch)

    class _BoomData(_FakeData):
        def get_quote(self, ticker):
            raise RuntimeError("yfinance down")

    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _BoomData()
    try:
        client.post("/trades", data={
            "ticker": "AMZN", "action": "buy", "quantity": 5, "price": 150,
        })
        page = client.get("/holdings")
        assert page.status_code == 200
        assert "AMZN" in page.text
        # Cost basis still shown
        assert "150.00" in page.text
    finally:
        client.app.dependency_overrides.clear()
