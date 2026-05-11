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


def test_add_holding_and_pl(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    fake = _FakeData(price=300.0)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        # add NVDA: 10 shares @ $200, current $300 → +$1000 (+50%)
        res = client.post("/holdings", data={
            "ticker": "NVDA", "quantity": 10, "avg_cost": 200, "notes": "core position",
        })
        assert res.status_code == 200
        assert "NVDA" in res.text
        assert "300.00" in res.text       # current price
        assert "3000.00" in res.text      # market value
        assert "+1000.00" in res.text     # pl dollars
        assert "+50.00" in res.text       # pl pct

        page = client.get("/holdings")
        assert "NVDA" in page.text
        assert "core position" in page.text
    finally:
        client.app.dependency_overrides.clear()


def test_add_holding_duplicate_rejected(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    fake = _FakeData()
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        client.post("/holdings", data={"ticker": "AAPL", "quantity": 5, "avg_cost": 180})
        res = client.post("/holdings", data={"ticker": "AAPL", "quantity": 3, "avg_cost": 200})
        assert res.status_code == 409
    finally:
        client.app.dependency_overrides.clear()


def test_add_holding_invalid_inputs(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    fake = _FakeData()
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        # bad ticker
        res = client.post("/holdings", data={"ticker": "  ", "quantity": 1, "avg_cost": 100})
        assert res.status_code == 422
        # negative quantity
        res = client.post("/holdings", data={"ticker": "TSLA", "quantity": -1, "avg_cost": 100})
        assert res.status_code == 422
        # zero cost
        res = client.post("/holdings", data={"ticker": "TSLA", "quantity": 1, "avg_cost": 0})
        assert res.status_code == 422
    finally:
        client.app.dependency_overrides.clear()


def test_update_holding(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    fake = _FakeData(price=400.0)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        client.post("/holdings", data={"ticker": "META", "quantity": 5, "avg_cost": 300})
        from sqlalchemy import text

        from marketpulse.db.base import get_engine
        with get_engine().connect() as conn:
            row_id = conn.execute(text("SELECT id FROM holdings WHERE ticker='META'")).scalar_one()
        # update quantity and avg_cost
        res = client.post(f"/holdings/{row_id}/update", data={
            "quantity": 10, "avg_cost": 350, "notes": "added more",
        })
        assert res.status_code == 200
        assert "10" in res.text
        assert "350.00" in res.text
        assert "added more" in res.text
    finally:
        client.app.dependency_overrides.clear()


def test_delete_holding(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    fake = _FakeData()
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        client.post("/holdings", data={"ticker": "GOOG", "quantity": 4, "avg_cost": 150})
        from sqlalchemy import text

        from marketpulse.db.base import get_engine
        with get_engine().connect() as conn:
            row_id = conn.execute(text("SELECT id FROM holdings WHERE ticker='GOOG'")).scalar_one()
        res = client.delete(f"/holdings/{row_id}")
        assert res.status_code == 200
        assert "GOOG" not in client.get("/holdings").text
    finally:
        client.app.dependency_overrides.clear()


def test_holdings_resilient_to_quote_failure(client: TestClient, monkeypatch):
    """If yfinance fails for a ticker, the row still renders with cost-basis info."""
    _login(client, monkeypatch)

    class _BoomData(_FakeData):
        def get_quote(self, ticker):
            raise RuntimeError("yfinance down")

    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _BoomData()
    try:
        res = client.post("/holdings", data={"ticker": "AMZN", "quantity": 5, "avg_cost": 150})
        assert res.status_code == 200
        assert "AMZN" in res.text
        # Cost basis still shown
        assert "150.00" in res.text
    finally:
        client.app.dependency_overrides.clear()
