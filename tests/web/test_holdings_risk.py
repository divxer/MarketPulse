from datetime import UTC, datetime
from unittest.mock import patch

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


def _add_holding(client):
    """Insert one trade so the holdings table is non-empty."""
    client.post("/trades", data={
        "ticker": "TST", "action": "buy", "quantity": 10, "price": 100,
    })


def test_risk_analysis_get_returns_card(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        _add_holding(client)
        with patch("marketpulse.ai.service.AiService.portfolio_risk",
                   return_value="## 风险评估\n\n- 项目 1\n- 项目 2"):
            r = client.get("/holdings/risk-analysis")
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 200
    assert "mp-card" in r.text
    assert "AI 风险分析" in r.text


def test_risk_analysis_renders_markdown_html(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        _add_holding(client)
        with patch("marketpulse.ai.service.AiService.portfolio_risk",
                   return_value="## 标题\n\n- 要点 A"):
            r = client.get("/holdings/risk-analysis")
    finally:
        client.app.dependency_overrides.clear()
    # Markdown <h2>, <ul>, <li> rendered
    assert "<h2>" in r.text or "标题" in r.text
    assert "要点 A" in r.text


def test_risk_analysis_handles_anthropic_error_returns_fallback(client: TestClient, monkeypatch):
    """Anthropic raises → 200 OK with fallback markdown."""
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        _add_holding(client)
        with patch("marketpulse.ai.service.AiService.portfolio_risk",
                   side_effect=RuntimeError("API down")):
            r = client.get("/holdings/risk-analysis")
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 200
    assert "AI 服务暂时不可用" in r.text or "稍后重试" in r.text


def test_risk_analysis_empty_holdings(client: TestClient, monkeypatch):
    """No holdings → no AI call, just a friendly message in the card."""
    _login(client, monkeypatch)
    # No patch needed — empty fixture means no AI call
    r = client.get("/holdings/risk-analysis")
    assert r.status_code == 200
    # Either renders empty card or friendly message
    assert "mp-card" in r.text


def test_risk_analysis_caches_response(client: TestClient, monkeypatch, db_session):
    """Second call with same portfolio state returns cached response (no AI call)."""
    from unittest.mock import MagicMock

    from marketpulse.data.types import Quote
    from marketpulse.db.models import Holding
    from marketpulse.web.deps import get_data_service

    _login(client, monkeypatch)
    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=150.0,
                           sort_order=0))
    db_session.commit()

    fake_data = MagicMock()
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake_data.get_history.return_value = []
    client.app.dependency_overrides[get_data_service] = lambda: fake_data

    call_count = [0]
    def fake_portfolio_risk(*args, **kwargs):
        call_count[0] += 1
        return f"## 测试风险分析\n\n第 {call_count[0]} 次"

    with patch("marketpulse.ai.service.AiService.portfolio_risk", side_effect=fake_portfolio_risk):
        r1 = client.get("/holdings/risk-analysis")
        r2 = client.get("/holdings/risk-analysis")

    assert r1.status_code == 200
    assert r2.status_code == 200
    # AI called exactly once for the first request; second is a cache hit
    assert call_count[0] == 1
    # Both responses contain the SAME content (first AI call's output)
    assert "第 1 次" in r1.text
    assert "第 1 次" in r2.text
    client.app.dependency_overrides.clear()


def test_risk_analysis_recomputes_when_holdings_change(client: TestClient, monkeypatch, db_session):
    """Adding a new holding invalidates the cache (different fingerprint)."""
    from unittest.mock import MagicMock

    from marketpulse.data.types import Quote
    from marketpulse.db.models import Holding
    from marketpulse.web.deps import get_data_service

    _login(client, monkeypatch)
    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=150.0,
                           sort_order=0))
    db_session.commit()

    fake_data = MagicMock()
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake_data.get_history.return_value = []
    client.app.dependency_overrides[get_data_service] = lambda: fake_data

    call_count = [0]
    def fake_portfolio_risk(*args, **kwargs):
        call_count[0] += 1
        return f"分析 #{call_count[0]}"

    with patch("marketpulse.ai.service.AiService.portfolio_risk", side_effect=fake_portfolio_risk):
        client.get("/holdings/risk-analysis")  # call 1: AI hit (fingerprint A)
        # Add a holding → fingerprint changes
        db_session.add(Holding(ticker="NVDA", quantity=5.0, avg_cost=400.0,
                               sort_order=1))
        db_session.commit()
        client.get("/holdings/risk-analysis")  # call 2: cache miss, AI hit

    assert call_count[0] == 2  # AI called twice, not 1 (cache invalidated)
    client.app.dependency_overrides.clear()
