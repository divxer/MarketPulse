"""Tests for /lab/ai-track route + shell."""
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed_events(db, *, count=10, ticker="AAPL", subtype="bullish", excess=0.03):
    for d in range(count):
        e = EvaluationEvent(
            event_type="ai_analysis", subtype=subtype, ticker=ticker,
            event_time=datetime.now(UTC) - timedelta(days=d),
            event_price=100.0,
            payload={"source": "stock_analysis", "prompt_version": "v3"},
        )
        db.add(e)
        db.flush()
        db.add(EvaluationOutcome(
            event_id=e.id, horizon_trading_days=5,
            event_price=100.0, horizon_price=103.0,
            horizon_date=date.today(), forward_return=0.031,
            benchmark_ticker="SPY", benchmark_forward_return=0.001,
            excess_return=excess,
        ))
    db.commit()


def test_lab_renders_placeholder_when_no_data(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track")
    assert r.status_code == 200
    assert "积累中" in r.text or "至少 7 个交易日" in r.text


def test_lab_uses_2400_max_width(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track")
    assert "max-w-[2400px]" in r.text


def test_lab_renders_anchors_when_data_present(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_events(db_session)
    r = client.get("/lab/ai-track")
    assert "mp-ai-track-kpi" in r.text
    assert "mp-ai-track-body" in r.text


def test_lab_invalid_horizon_returns_422(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track?horizon=3")
    assert r.status_code == 422


def test_lab_since_days_all_no_date_filter(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    # Seed very old event (200 days ago)
    e = EvaluationEvent(
        event_type="ai_analysis", subtype="bullish", ticker="OLD",
        event_time=datetime.now(UTC) - timedelta(days=200),
        event_price=100.0,
        payload={"source": "stock_analysis", "prompt_version": "v3"},
    )
    db_session.add(e)
    db_session.flush()
    db_session.add(EvaluationOutcome(
        event_id=e.id, horizon_trading_days=5,
        event_price=100.0, horizon_price=103.0,
        horizon_date=date.today(), forward_return=0.031,
        benchmark_ticker="SPY", benchmark_forward_return=0.001, excess_return=0.03,
    ))
    db_session.commit()
    r = client.get("/lab/ai-track?since_days=all")
    assert r.status_code == 200
    # OLD ticker should appear in ticker table (no date filter)
    assert "OLD" in r.text


def test_lab_hero_renders_h1(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_events(db_session)
    r = client.get("/lab/ai-track")
    assert "AI Hit Rate" in r.text
    assert "实验室" in r.text or "AI 评估" in r.text


def test_lab_renders_4_kpi_strip_when_data_present(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_events(db_session)
    r = client.get("/lab/ai-track")
    # KPI labels
    assert "总 verdicts" in r.text
    assert "Hit Rate" in r.text
    assert "Avg Excess" in r.text


def test_lab_trend_chart_renders_svg_polyline_with_enough_data(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_events(db_session, count=30)
    r = client.get("/lab/ai-track")
    assert "<svg" in r.text
    assert "<polyline" in r.text


def test_lab_recent_events_table_renders_rows(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_events(db_session, count=5)
    r = client.get("/lab/ai-track")
    assert "<table" in r.text
    assert "mp-ai-track-recent" in r.text
