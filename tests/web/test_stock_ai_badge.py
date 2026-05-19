"""AI hit-rate badge on /stock/{ticker} page."""
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.data.types import Quote
from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


class _FakeData:
    def get_quote(self, ticker):
        return Quote(
            ticker=ticker, price=100.0, change_pct=1.0,
            volume=10, avg_volume_20d=10, fetched_at=datetime.now(UTC),
        )

    def get_history(self, ticker, period="60d"):
        return []

    def get_news(self, ticker, limit=10):
        return []


def _set_fake_data(client):
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()


def _clear_overrides(client):
    client.app.dependency_overrides.clear()


def _ev_with_outcome(db, *, ticker, subtype, excess, days_ago=5):
    e = EvaluationEvent(
        event_type="ai_analysis",
        subtype=subtype,
        ticker=ticker,
        event_time=datetime.now(UTC) - timedelta(days=days_ago),
        event_price=100.0,
        payload={"source": "stock_analysis", "prompt_version": "v3"},
    )
    db.add(e)
    db.flush()
    o = EvaluationOutcome(
        event_id=e.id, horizon_trading_days=5,
        event_price=100.0, horizon_price=100.0,
        horizon_date=date.today(),
        forward_return=excess + 0.001,
        benchmark_ticker="SPY", benchmark_forward_return=0.001,
        excess_return=excess,
    )
    db.add(o)
    return e


def test_stock_page_no_badge_when_n_total_zero(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    _set_fake_data(client)
    try:
        r = client.get("/stock/AAPL")
        assert r.status_code == 200
        assert "mp-ai-badge" not in r.text
    finally:
        _clear_overrides(client)


def test_stock_page_pending_badge_when_n_below_5(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _set_fake_data(client)
    for _ in range(3):
        _ev_with_outcome(db_session, ticker="AAPL", subtype="bullish", excess=0.03)
    db_session.commit()
    try:
        r = client.get("/stock/AAPL")
        assert r.status_code == 200
        assert "mp-ai-badge--pending" in r.text
        assert "积累中" in r.text
    finally:
        _clear_overrides(client)


def test_stock_page_good_badge_when_hit_rate_above_60(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _set_fake_data(client)
    # 10 events, 8 hits → 80%
    for i in range(10):
        _ev_with_outcome(db_session, ticker="AAPL", subtype="bullish",
                         excess=0.03 if i < 8 else -0.02)
    db_session.commit()
    try:
        r = client.get("/stock/AAPL")
        assert r.status_code == 200
        assert "mp-ai-badge--good" in r.text
    finally:
        _clear_overrides(client)


def test_stock_page_badge_links_to_lab(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _set_fake_data(client)
    for _ in range(8):
        _ev_with_outcome(db_session, ticker="AAPL", subtype="bullish", excess=0.03)
    db_session.commit()
    try:
        r = client.get("/stock/AAPL")
        assert r.status_code == 200
        assert 'href="/lab/ai-track?ticker=AAPL"' in r.text
    finally:
        _clear_overrides(client)


def test_stock_page_bad_badge_when_hit_rate_below_40(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _set_fake_data(client)
    # 10 events, 2 hits → 20%
    for i in range(10):
        _ev_with_outcome(db_session, ticker="AAPL", subtype="bullish",
                         excess=0.03 if i < 2 else -0.02)
    db_session.commit()
    try:
        r = client.get("/stock/AAPL")
        assert r.status_code == 200
        assert "mp-ai-badge--bad" in r.text
    finally:
        _clear_overrides(client)
