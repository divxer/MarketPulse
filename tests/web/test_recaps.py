"""/recaps grid view tests."""
import json
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.models import DailyRecap


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed(db_session, recap_date, *, status="success",
          holdings_totals=None, commentary="..."):
    r = DailyRecap(
        recap_date=recap_date,
        generation_status=status,
        ai_commentary_text=commentary,
        holdings_totals_json=json.dumps(holdings_totals) if holdings_totals else None,
        generated_at=datetime(2026, 5, 12, 20, tzinfo=UTC) if status == "success" else None,
    )
    db_session.add(r)
    db_session.commit()
    return r


def test_recaps_grid_renders_mp_recaps_card(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session, date(2026, 5, 12))
    r = client.get("/recaps")
    assert r.status_code == 200
    assert "mp-recaps-card" in r.text
    assert "Recap History" in r.text


def test_recaps_grid_shows_pl_when_data_present(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session, date(2026, 5, 12), holdings_totals={
        "cost": 10000.0, "market_value": 10500.0,
        "pl_dollars": 500.0, "pl_pct": 5.0,
    })
    r = client.get("/recaps")
    assert "+500" in r.text


def test_recaps_grid_handles_missing_pl(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session, date(2026, 5, 12), holdings_totals=None)
    r = client.get("/recaps")
    assert "无盈亏数据" in r.text


def test_recaps_grid_status_chips_color_coded(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session, date(2026, 5, 12), status="success")
    _seed(db_session, date(2026, 5, 11), status="failed")
    _seed(db_session, date(2026, 5, 10), status="pending")
    r = client.get("/recaps")
    assert "mp-chip--success" in r.text
    assert "mp-chip--failed" in r.text
    assert "mp-chip--pending" in r.text


def test_recaps_grid_empty_state(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/recaps")
    assert "暂无复盘记录" in r.text
