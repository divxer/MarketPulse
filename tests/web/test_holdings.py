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


def test_holdings_dashboard_shows_kpis_and_allocation(client: TestClient, monkeypatch):
    """Dashboard shows total cost/market value/P&L KPIs and per-ticker allocation."""
    _login(client, monkeypatch)
    fake = _FakeData(price=300.0)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        client.post("/trades", data={
            "ticker": "AAA", "action": "buy", "quantity": 10, "price": 200,
        })
        page = client.get("/holdings")
        assert page.status_code == 200
        # KPI labels
        assert "总成本" in page.text
        assert "市值" in page.text
        assert "未实现盈亏" in page.text
        assert "已实现盈亏" in page.text
        # Allocation card
        assert "持仓分布" in page.text
        # Contribution ranking
        assert "贡献度排行" in page.text
        # AI risk analysis card
        assert "AI 风险分析" in page.text
    finally:
        client.app.dependency_overrides.clear()


def test_holdings_risk_analysis_endpoint(client: TestClient, monkeypatch):
    """POST /holdings/risk-analysis calls AI and renders markdown response."""
    _login(client, monkeypatch)
    fake = _FakeData()
    class _FakeAi:
        def portfolio_risk(self, **kwargs):
            return "**集中度风险**:测试输出\n\n仅占位用于单测。"
    from marketpulse.web.deps import get_ai_service, get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    client.app.dependency_overrides[get_ai_service] = lambda: _FakeAi()
    try:
        client.post("/trades", data={
            "ticker": "BBB", "action": "buy", "quantity": 5, "price": 100,
        })
        res = client.post("/holdings/risk-analysis")
        assert res.status_code == 200
        assert "集中度风险" in res.text
    finally:
        client.app.dependency_overrides.clear()


def test_holdings_risk_analysis_empty_portfolio(client: TestClient, monkeypatch):
    """Empty portfolio returns a friendly skip message without calling AI."""
    _login(client, monkeypatch)
    fake = _FakeData()
    class _FakeAi:
        def portfolio_risk(self, **kwargs):
            raise AssertionError("should not be called when no holdings")
    from marketpulse.web.deps import get_ai_service, get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    client.app.dependency_overrides[get_ai_service] = lambda: _FakeAi()
    try:
        res = client.post("/holdings/risk-analysis")
        assert res.status_code == 200
        assert "暂无持仓" in res.text
    finally:
        client.app.dependency_overrides.clear()


def test_risk_analysis_renders_markdown_to_html(client: TestClient, monkeypatch):
    """`**bold**` and `## heading` in AI output must become <strong> and <h2>."""
    _login(client, monkeypatch)
    fake = _FakeData()
    class _MdAi:
        def portfolio_risk(self, **kwargs):
            return "## 风险\n\n这是一段 **粗体** 文字。\n\n- 第一项\n- 第二项"
    from marketpulse.web.deps import get_ai_service, get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    client.app.dependency_overrides[get_ai_service] = lambda: _MdAi()
    try:
        client.post("/trades", data={
            "ticker": "MDX", "action": "buy", "quantity": 1, "price": 10,
        })
        res = client.post("/holdings/risk-analysis")
        assert res.status_code == 200
        # Heading rendered to <h2>, bold to <strong>, list to <li>
        assert "<h2>" in res.text and "风险" in res.text
        assert "<strong>" in res.text and "粗体" in res.text
        assert "<li>" in res.text and "第一项" in res.text
        # Literal markdown syntax should NOT survive
        assert "**粗体**" not in res.text
        assert "## 风险" not in res.text
    finally:
        client.app.dependency_overrides.clear()
