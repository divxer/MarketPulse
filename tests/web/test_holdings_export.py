import csv
from datetime import UTC, datetime
from io import StringIO
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.data.types import Quote
from marketpulse.db.models import Holding


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _mock_data_service(client):
    """Override get_data_service to return a fake that yields a quote."""
    from marketpulse.web.deps import get_data_service

    fake = MagicMock()
    fake.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake.get_history.return_value = []
    client.app.dependency_overrides[get_data_service] = lambda: fake
    return fake


def test_export_csv_content_type(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")


def test_export_csv_filename_header(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings/export.csv")
    cd = r.headers["content-disposition"]
    assert "attachment" in cd
    assert "holdings-" in cd and ".csv" in cd


def test_export_csv_header_row(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings/export.csv")
    first_line = r.text.split("\n")[0]
    expected = (
        "ticker,name,sector,quantity,avg_cost,current_price,market_value,"
        "cost_basis,unrealized_pl,unrealized_pl_pct,dividends_received"
    )
    assert first_line == expected


def test_export_csv_includes_holding_rows(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _mock_data_service(client)
    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0, sector="Technology"))
    db_session.commit()
    r = client.get("/holdings/export.csv")
    rows = list(csv.reader(StringIO(r.text)))
    assert len(rows) >= 2
    data_row = rows[1]
    assert data_row[0] == "AAPL"
    assert data_row[2] == "Technology"
    client.app.dependency_overrides.clear()


def test_export_csv_empty_holdings_header_only(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings/export.csv")
    lines = [ln for ln in r.text.split("\n") if ln.strip()]
    assert len(lines) == 1  # header only
