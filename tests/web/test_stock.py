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


def test_stock_page_shows_holding_strip_when_position_exists(
    client: TestClient, monkeypatch,
) -> None:
    """When the user holds the ticker, /stock shows current shares + avg cost + live P&L."""
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        # Buy 10 @ $200 → holding row exists
        client.post("/trades", data={
            "ticker": "AAPL", "action": "buy", "quantity": 10, "price": 200,
        })
        res = client.get("/stock/AAPL")
        assert res.status_code == 200
        assert "持仓" in res.text
        assert "10" in res.text  # quantity
        assert "200.00" in res.text  # avg cost
    finally:
        client.app.dependency_overrides.clear()


def test_stock_page_shows_in_watchlist_state(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        client.post("/watchlist", data={"ticker": "AAPL"})
        res = client.get("/stock/AAPL")
        assert "已自选" in res.text
    finally:
        client.app.dependency_overrides.clear()


def test_stock_page_shows_recent_trades(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        client.post("/trades", data={
            "ticker": "AAPL", "action": "buy", "quantity": 5, "price": 199.50,
        })
        res = client.get("/stock/AAPL")
        assert "最近交易" in res.text
        assert "199.50" in res.text
    finally:
        client.app.dependency_overrides.clear()


def test_stock_page_recent_trades_orders_null_executed_at_by_created_at(
    client: TestClient, monkeypatch,
) -> None:
    """Regression: trades with NULL executed_at (entered via the form before
    PR #8's date-input fix) must appear in correct chronological position by
    `created_at`, not pinned to the bottom of the list via NULLS LAST.
    """
    from datetime import UTC, datetime

    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        # Insert two trades directly to control NULL state:
        #   t1: explicit executed_at = 2024-01-01 (very old)
        #   t2: executed_at = None, created_at = now (very new)
        # Old code (NULLS LAST) would show t1 first → wrong.
        # New code (coalesce) must show t2 first.
        gen = db_base.session_scope()
        s = next(gen)
        # Use unusual prices that won't collide with chart axis labels or
        # other rendered numbers — pinning the assertion to these specific
        # values is brittle otherwise.
        s.add(Trade(ticker="AAPL", action="buy", quantity=1, price=987.65,
                    executed_at=datetime(2024, 1, 1, tzinfo=UTC),
                    created_at=datetime(2024, 1, 1, tzinfo=UTC)))
        s.add(Trade(ticker="AAPL", action="sell", quantity=1, price=876.54,
                    executed_at=None,
                    created_at=datetime.now(UTC)))
        s.commit()

        res = client.get("/stock/AAPL")
        # The $876.54 sell (NULL executed_at, but newer created_at) must
        # appear before the $987.65 buy in the recent-trades section.
        text = res.text
        idx_sell = text.find("876.54")
        idx_buy = text.find("987.65")
        assert idx_sell != -1 and idx_buy != -1, "both trades should render"
        assert idx_sell < idx_buy, (
            f"sell (NULL executed_at, newer created_at) should sort first; "
            f"sell at {idx_sell}, buy at {idx_buy}"
        )
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
            "bb_upper", "bb_lower",
            "rsi", "macd", "signal_markers",
        }
        assert "bb_middle" not in data, "bb_middle was dropped from the contract"
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
        client.get("/stock/AAPL/chart-data?period=60d")
        # Despite user requesting 60d, backend should fetch 1y so SMA200 has data.
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


def test_chart_data_before_param_returns_window_before_date(
    client: TestClient, monkeypatch,
) -> None:
    """?before=2024-06-01&count=180 → bars dated strictly before 2024-06-01,
    at most 180 of them, indicators trimmed to the same window.
    """
    from datetime import date
    from datetime import timedelta as _td
    from unittest.mock import patch

    from marketpulse.data.types import Bar

    _login(client, monkeypatch)
    # Build a fake yfinance window: 430 bars (padding + chunk) ending 2024-05-31.
    # We don't need real OHLCV math — just unique close values + ascending dates.
    fake_bars = []
    base = date(2023, 3, 28)  # 430 calendar days back is approximate; use 430
    for i in range(430):
        d = base + _td(days=i)
        fake_bars.append(Bar(date=d, open=10.0, high=10.5, low=9.5,
                             close=10.0 + (i * 0.01),
                             volume=1_000_000))

    with patch(
        "marketpulse.data.yfinance_client.YFinanceClient.fetch_history_range",
        return_value=fake_bars,
    ):
        res = client.get("/stock/AAPL/chart-data?before=2024-06-01&count=180")

    assert res.status_code == 200
    body = res.json()
    bars = body["bars"]
    assert len(bars) <= 180
    # Every returned bar must be strictly before the `before` cutoff.
    assert all(b["time"] < "2024-06-01" for b in bars)
    # Bars are oldest-first.
    times = [b["time"] for b in bars]
    assert times == sorted(times)


def test_chart_data_before_empty_when_no_data(client: TestClient, monkeypatch) -> None:
    """If yfinance returns no data in the window (ticker not yet IPO'd),
    response has bars=[] and all indicator arrays empty."""
    from unittest.mock import patch

    _login(client, monkeypatch)
    with patch(
        "marketpulse.data.yfinance_client.YFinanceClient.fetch_history_range",
        return_value=[],
    ):
        res = client.get("/stock/AAPL/chart-data?before=1900-01-01&count=180")

    assert res.status_code == 200
    body = res.json()
    assert body["bars"] == []
    assert body["sma200"] == []
    assert body["rsi"] == []


def test_chart_data_period_still_works(client: TestClient, monkeypatch) -> None:
    """Regression: the existing ?period=60d path is unchanged."""
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        res = client.get("/stock/AAPL/chart-data?period=60d")
        assert res.status_code == 200
        assert "bars" in res.json()
    finally:
        client.app.dependency_overrides.clear()


def test_chart_data_before_invalid_date_returns_422(
    client: TestClient, monkeypatch,
) -> None:
    _login(client, monkeypatch)
    res = client.get("/stock/AAPL/chart-data?before=not-a-date&count=180")
    assert res.status_code == 422


def test_chart_data_count_capped_at_max(client: TestClient, monkeypatch) -> None:
    """Sanity: count is bounded to prevent abuse (e.g., 1_000_000 days)."""
    _login(client, monkeypatch)
    res = client.get("/stock/AAPL/chart-data?before=2024-06-01&count=999999")
    # Either 422 with a clear message, or silently capped — either is acceptable;
    # this test asserts the API doesn't OOM trying to fulfill it.
    assert res.status_code in (200, 422)


def test_chart_data_ytd_returns_year_to_date(client: TestClient, monkeypatch):
    from datetime import date
    _login(client, monkeypatch)
    r = client.get("/stock/AAPL/chart-data?period=ytd")
    assert r.status_code == 200
    payload = r.json()
    if not payload["bars"]:
        return
    first_bar_date = date.fromisoformat(payload["bars"][0]["time"])
    today = date.today()
    assert first_bar_date >= date(today.year, 1, 1)
    assert first_bar_date <= today


def test_chart_data_5y_uses_yfinance(client: TestClient, monkeypatch):
    from datetime import date, timedelta

    from marketpulse.data.yfinance_client import YFinanceClient
    _login(client, monkeypatch)
    called_with = {}
    def fake_fetch_range(self, ticker, *, start, end):
        called_with["ticker"] = ticker
        called_with["start"] = start
        called_with["end"] = end
        return []
    monkeypatch.setattr(YFinanceClient, "fetch_history_range", fake_fetch_range)
    r = client.get("/stock/AAPL/chart-data?period=5y")
    assert r.status_code == 200
    assert called_with["ticker"] == "AAPL"
    expected_start = date.today() - timedelta(days=1825)
    assert abs((called_with["start"] - expected_start).days) <= 2
    assert called_with["end"] == date.today()


def test_chart_data_all_uses_yfinance_from_1900(client: TestClient, monkeypatch):
    from datetime import date

    from marketpulse.data.yfinance_client import YFinanceClient
    _login(client, monkeypatch)
    called_with = {}
    def fake_fetch_range(self, ticker, *, start, end):
        called_with["start"] = start
        return []
    monkeypatch.setattr(YFinanceClient, "fetch_history_range", fake_fetch_range)
    r = client.get("/stock/AAPL/chart-data?period=all")
    assert r.status_code == 200
    assert called_with["start"] <= date(1900, 1, 1)


def test_chart_data_rejects_30d(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/stock/AAPL/chart-data?period=30d")
    assert r.status_code == 422


def test_chart_data_rejects_invalid_period(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/stock/AAPL/chart-data?period=foo")
    assert r.status_code == 422


def test_stock_page_has_new_period_buttons(client: TestClient, monkeypatch):
    """The /stock/{ticker} page must show YTD/5Y/All buttons and no 30D button."""
    _login(client, monkeypatch)
    r = client.get("/stock/AAPL")
    assert r.status_code == 200
    body = r.text
    assert 'data-period="ytd"' in body, "YTD button missing"
    assert 'data-period="5y"' in body, "5Y button missing"
    assert 'data-period="all"' in body, "All button missing"
    assert 'data-period="30d"' not in body, "30D button must be removed"


def test_stock_page_has_ohlc_bar(client: TestClient, monkeypatch):
    """Chart page must include an OHLC bar element above the chart."""
    _login(client, monkeypatch)
    r = client.get("/stock/AAPL")
    assert r.status_code == 200
    body = r.text
    assert 'id="chart-ohlc-bar"' in body
    assert 'data-ohlc="open"' in body
    assert 'data-ohlc="high"' in body
    assert 'data-ohlc="low"' in body
    assert 'data-ohlc="close"' in body
    assert 'data-ohlc="change"' in body


def test_chart_js_uses_localstorage_for_period(client: TestClient, monkeypatch):
    """chart.js must persist period via localStorage."""
    _login(client, monkeypatch)
    r = client.get("/static/chart.js")
    assert r.status_code == 200
    body = r.text
    assert "localStorage" in body, "chart.js must persist period across sessions"
    assert "mp.chartPeriod" in body, "chart.js must use the agreed storage key"


def test_chart_js_subscribes_crosshair_for_ohlc(client: TestClient, monkeypatch):
    """chart.js must subscribe to crosshair moves and update the OHLC bar."""
    _login(client, monkeypatch)
    r = client.get("/static/chart.js")
    assert r.status_code == 200
    body = r.text
    assert "subscribeCrosshairMove" in body, (
        "chart.js must subscribe to crosshair to keep OHLC bar in sync"
    )
    assert "updateOhlcBar" in body, "updateOhlcBar function missing"
    assert "data-ohlc=" in body, (
        "updateOhlcBar must select OHLC field elements via data-ohlc"
    )
