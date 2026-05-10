from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from marketpulse.ai.types import AnalysisResult
from marketpulse.auth.password import hash_password
from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


class _FakeData:
    def get_quote(self, ticker):
        return Quote(ticker=ticker, price=100, change_pct=1, volume=10,
                     avg_volume_20d=10, fetched_at=datetime.now(UTC))
    def get_history(self, ticker, period="60d"):
        return [Bar(date=date(2026, 5, 7), open=1, high=2, low=0.5, close=1.5, volume=100)]
    def get_news(self, ticker, limit=10):
        return [NewsItem(ticker=ticker, headline="hello", url="u",
                         published_at=datetime.now(UTC), source="s")]
    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, market_cap=1, pe_ratio=10, eps=1,
                            sector="t", industry="i")


class _FakeAi:
    def analyze(self, ticker):
        return AnalysisResult(
            ticker=ticker, model="m", prompt_version="v",
            response_markdown="## Fundamentals\nstuff", requested_at=datetime.now(UTC),
        )


def test_stock_page(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        res = client.get("/stock/AAPL")
        assert res.status_code == 200
        assert "AAPL" in res.text
    finally:
        client.app.dependency_overrides.clear()


def test_stock_analyze(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_ai_service, get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    client.app.dependency_overrides[get_ai_service] = lambda: _FakeAi()
    try:
        res = client.post("/stock/AAPL/analyze")
        assert res.status_code == 200
        assert "Fundamentals" in res.text
    finally:
        client.app.dependency_overrides.clear()


def test_stock_analyze_failure_renders_error_fragment(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)

    class _BoomAi:
        def analyze(self, ticker):
            raise RuntimeError("anthropic unavailable")

    from marketpulse.web.deps import get_ai_service, get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    client.app.dependency_overrides[get_ai_service] = lambda: _BoomAi()
    try:
        res = client.post("/stock/AAPL/analyze")
        assert res.status_code == 200
        assert "AI analysis failed" in res.text
        assert "anthropic unavailable" in res.text
        assert "Retry" in res.text
    finally:
        client.app.dependency_overrides.clear()
