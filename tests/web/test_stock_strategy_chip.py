"""Strategy chip in /stock AI card head."""
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.data.types import Quote
from marketpulse.db.models import AiAnalysis


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


_VERDICTS = (
    '{"ticker":"AAPL","verdict":"bullish","rationale":"x"}'
)


def _seed_cached(db, *, ticker="AAPL", strategy="momentum_breakout"):
    now = datetime.now(UTC)
    db.add(AiAnalysis(
        ticker=ticker,
        model="claude-sonnet-4-6",
        prompt_version="analysis-v4",
        strategy=strategy,
        strategy_version="v1",
        input_data_json="{}",
        response_markdown=f"## body\n\nVERDICTS_JSON: {_VERDICTS}",
        requested_at=now,
        expires_at=now + timedelta(hours=24),
    ))
    db.commit()


def test_stock_page_renders_strategy_chip_when_analysis_cached(
    client: TestClient, monkeypatch, db_session,
):
    _login(client, monkeypatch)
    _set_fake_data(client)
    _seed_cached(db_session)
    try:
        r = client.get("/stock/AAPL")
        assert r.status_code == 200
        assert "mp-chip--strategy" in r.text
        assert "动量突破" in r.text  # display_name
    finally:
        _clear_overrides(client)


def test_stock_page_no_strategy_chip_when_no_cached_analysis(
    client: TestClient, monkeypatch,
):
    _login(client, monkeypatch)
    _set_fake_data(client)
    try:
        r = client.get("/stock/AAPL")
        assert r.status_code == 200
        assert "mp-chip--strategy" not in r.text
    finally:
        _clear_overrides(client)


def test_stock_page_chip_uses_strategy_display_name_not_internal_name(
    client: TestClient, monkeypatch, db_session,
):
    _login(client, monkeypatch)
    _set_fake_data(client)
    _seed_cached(db_session, strategy="fundamental_value")
    try:
        r = client.get("/stock/AAPL")
        assert r.status_code == 200
        # Display name should appear, not internal snake_case
        assert "价值分析" in r.text
    finally:
        _clear_overrides(client)
