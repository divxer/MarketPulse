from datetime import UTC, date, datetime, timedelta

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
        assert "AI 分析失败" in res.text
        assert "anthropic unavailable" in res.text
        assert "重试" in res.text
    finally:
        client.app.dependency_overrides.clear()


def _make_bars(n: int, start_close: float = 100.0) -> list[Bar]:
    today = date.today()
    return [
        Bar(date=today - timedelta(days=n - i),
            open=start_close + i, high=start_close + i + 1,
            low=start_close + i - 1, close=start_close + i,
            volume=1_000_000)
        for i in range(n)
    ]


class _FakeDataChart:
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars
        self.last_period: str | None = None

    def get_quote(self, ticker: str) -> Quote:
        return Quote(ticker=ticker, price=100.0, change_pct=0,
                     volume=1, avg_volume_20d=1, fetched_at=datetime.now(UTC))

    def get_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        self.last_period = period
        return self.bars

    def get_news(self, ticker: str, limit: int = 10): return []


def test_chart_data_returns_expected_keys(client, monkeypatch) -> None:
    _login(client, monkeypatch)
    fake = _FakeDataChart(_make_bars(300))
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        r = client.get("/stock/AAPL/chart-data?period=60d")
        assert r.status_code == 200
        data = r.json()
        assert set(data.keys()) >= {
            "bars", "ema12", "ema26", "sma50", "sma200",
            "bb_upper", "bb_middle", "bb_lower",
            "rsi", "macd", "signal_markers",
        }
        assert isinstance(data["bars"], list)
        assert data["bars"][0].keys() >= {"time", "open", "high", "low", "close", "volume"}
        assert isinstance(data["macd"], dict)
        assert set(data["macd"].keys()) == {"line", "signal", "histogram"}
    finally:
        client.app.dependency_overrides.clear()


def test_chart_data_fetches_with_200d_headroom_for_sma(client, monkeypatch) -> None:
    _login(client, monkeypatch)
    fake = _FakeDataChart(_make_bars(300))
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        client.get("/stock/AAPL/chart-data?period=30d")
        # Despite user requesting 30d, backend should fetch 1y so SMA200 has data.
        assert fake.last_period == "1y"
    finally:
        client.app.dependency_overrides.clear()


def test_chart_data_unknown_period_returns_422(client, monkeypatch) -> None:
    _login(client, monkeypatch)
    fake = _FakeDataChart(_make_bars(10))
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        r = client.get("/stock/AAPL/chart-data?period=banana")
        assert r.status_code == 422
    finally:
        client.app.dependency_overrides.clear()


def test_chart_data_empty_bars_returns_empty_arrays(client, monkeypatch) -> None:
    _login(client, monkeypatch)
    fake = _FakeDataChart([])
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        r = client.get("/stock/AAPL/chart-data?period=60d")
        assert r.status_code == 200
        data = r.json()
        assert data["bars"] == []
        assert data["ema12"] == []
        assert data["signal_markers"] == []
    finally:
        client.app.dependency_overrides.clear()


def test_chart_data_sets_cache_control_header(client, monkeypatch) -> None:
    _login(client, monkeypatch)
    fake = _FakeDataChart(_make_bars(10))
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        r = client.get("/stock/AAPL/chart-data?period=60d")
        assert "max-age=300" in r.headers.get("cache-control", "")
    finally:
        client.app.dependency_overrides.clear()
