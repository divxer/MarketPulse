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
        with patch("marketpulse.web.routes.holdings.backfill_holding_sectors", return_value=0):
            page = client.get("/holdings")
        assert page.status_code == 200
        assert "NVDA" in page.text
        assert "300.00" in page.text       # current price
        assert "3,000.00" in page.text     # market value (comma-formatted)
        assert "+1,000.00" in page.text    # pl dollars (comma-formatted)
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
        with patch("marketpulse.web.routes.holdings.backfill_holding_sectors", return_value=0):
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
        with patch("marketpulse.web.routes.holdings.backfill_holding_sectors", return_value=0):
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
    """GET /holdings/risk-analysis calls AI and renders markdown response."""
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
        res = client.get("/holdings/risk-analysis")
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
        res = client.get("/holdings/risk-analysis")
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
        res = client.get("/holdings/risk-analysis")
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


def test_post_dividend_records_to_db(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/dividends", data={
        "ticker": "TQQQ", "ex_date": "2024-03-20",
        "amount_per_share": 0.22, "total_amount": 6.02,
    })
    assert res.status_code == 200
    j = res.json()
    assert j["ticker"] == "TQQQ"
    assert j["ex_date"] == "2024-03-20"
    assert j["total_amount"] == 6.02


def test_post_dividend_bad_date_returns_422(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/dividends", data={
        "ticker": "TQQQ", "ex_date": "not-a-date",
        "amount_per_share": 0.22, "total_amount": 6.02,
    })
    assert res.status_code == 422


def test_holdings_page_shows_total_dividends_kpi(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    fake = _FakeData()
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        client.post("/dividends", data={
            "ticker": "TQQQ", "ex_date": "2024-03-20",
            "amount_per_share": 0.22, "total_amount": 6.02,
        })
        page = client.get("/holdings")
        assert "累计分红" in page.text
        assert "+6.02" in page.text
    finally:
        client.app.dependency_overrides.clear()


def test_holdings_page_renders_with_phase_5d_context(client, monkeypatch):
    """Smoke test: page renders without UndefinedError after route extension."""
    _login(client, monkeypatch)
    r = client.get("/holdings")
    assert r.status_code == 200
    # The old template still references context keys; new keys are extra
    # and silently ignored by Jinja. Just verify no 500.
    assert "已实现盈亏" in r.text or "YTD" in r.text or "holdings" in r.text.lower()


def test_holdings_page_visual_anchors_present(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings")
    for cls in ("mp-holdings-hero", "mp-holdings-kpi",
                "mp-holdings-row3", "mp-holdings-table",
                "mp-holdings-bottom"):
        assert cls in r.text, f"missing {cls}"


def test_holdings_page_h1_renders(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings")
    assert "Holdings · Portfolio Overview" in r.text


def test_holdings_page_uses_2400_max_width(client, monkeypatch):
    """Like /stock and /trades, /holdings must override base.html's
    default max-w-5xl with max-w-[2400px]."""
    _login(client, monkeypatch)
    r = client.get("/holdings")
    assert "max-w-[2400px]" in r.text


def test_holdings_hero_renders_three_big_numbers(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings")
    assert "总市值" in r.text
    assert "未实现盈亏" in r.text
    assert "今日" in r.text


def test_holdings_donut_renders_svg(client, monkeypatch, db_session):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from marketpulse.data.types import Quote
    from marketpulse.db.models import Holding

    _login(client, monkeypatch)

    # Override DataService to return valid quote so allocation isn't empty
    from marketpulse.web.deps import get_data_service
    fake = MagicMock()
    fake.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake.get_history.return_value = []
    client.app.dependency_overrides[get_data_service] = lambda: fake

    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0))
    db_session.commit()
    r = client.get("/holdings")
    assert "<svg" in r.text
    assert 'viewBox="0 0 100 100"' in r.text

    client.app.dependency_overrides.clear()


def test_holdings_kpi_strip_5_cards(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings")
    assert r.text.count("mp-kpi__value") == 5
    for label in ("总成本", "市值", "未实现盈亏", "已实现盈亏", "累计分红"):
        assert label in r.text


def test_holdings_allocation_card_renders(client, monkeypatch, db_session):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from marketpulse.data.types import Quote
    from marketpulse.db.models import Holding
    from marketpulse.web.deps import get_data_service

    _login(client, monkeypatch)
    fake = MagicMock()
    fake.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake.get_history.return_value = []
    client.app.dependency_overrides[get_data_service] = lambda: fake

    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0))
    db_session.commit()
    r = client.get("/holdings")
    assert "持仓分布 · 按代码" in r.text
    assert "mp-allocation-row" in r.text
    client.app.dependency_overrides.clear()


def test_holdings_sector_card_renders(client, monkeypatch, db_session):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from marketpulse.data.types import Quote
    from marketpulse.db.models import Holding
    from marketpulse.web.deps import get_data_service

    _login(client, monkeypatch)
    fake = MagicMock()
    fake.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake.get_history.return_value = []
    client.app.dependency_overrides[get_data_service] = lambda: fake

    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0, sector="Technology"))
    db_session.commit()
    r = client.get("/holdings")
    assert "板块分布" in r.text
    client.app.dependency_overrides.clear()


def test_holdings_sector_card_shows_unclassified(client, monkeypatch, db_session):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock, patch

    from marketpulse.data.types import Quote
    from marketpulse.db.models import Holding
    from marketpulse.web.deps import get_data_service

    _login(client, monkeypatch)
    fake = MagicMock()
    fake.get_quote.return_value = Quote(
        ticker="XYZ", price=150.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake.get_history.return_value = []
    client.app.dependency_overrides[get_data_service] = lambda: fake

    db_session.add(Holding(ticker="XYZ", quantity=1.0, avg_cost=100.0,
                           sort_order=0, sector=None))
    db_session.commit()
    # Prevent backfill from calling yfinance so sector stays None → "未分类"
    with patch("marketpulse.web.routes.holdings.backfill_holding_sectors"):
        r = client.get("/holdings")
    assert "未分类" in r.text
    client.app.dependency_overrides.clear()


def test_holdings_contributors_card_renders(client, monkeypatch, db_session):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from marketpulse.data.types import Quote
    from marketpulse.db.models import Holding
    from marketpulse.web.deps import get_data_service

    _login(client, monkeypatch)
    fake = MagicMock()
    fake.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake.get_history.return_value = []
    client.app.dependency_overrides[get_data_service] = lambda: fake

    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0))
    db_session.commit()
    r = client.get("/holdings")
    assert "盈亏贡献" in r.text or "贡献排行" in r.text
    client.app.dependency_overrides.clear()


def test_holdings_table_14_columns(client, monkeypatch, db_session):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock, patch

    from marketpulse.data.types import Quote
    from marketpulse.db.models import Holding
    from marketpulse.web.deps import get_data_service

    _login(client, monkeypatch)
    fake = MagicMock()
    fake.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake.get_history.return_value = []
    client.app.dependency_overrides[get_data_service] = lambda: fake

    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0))
    db_session.commit()
    with patch("marketpulse.web.routes.holdings.backfill_holding_sectors", return_value=0):
        r = client.get("/holdings")
    th_count = r.text.count("<th")
    assert th_count >= 14, f"expected >= 14 <th>, got {th_count}"
    client.app.dependency_overrides.clear()


def test_holdings_table_delete_uses_int_id(client, monkeypatch, db_session):
    """DELETE URL must use r.id (int), not r.ticker (string)."""
    import re
    from datetime import UTC, datetime
    from unittest.mock import MagicMock, patch

    from marketpulse.data.types import Quote
    from marketpulse.db.models import Holding
    from marketpulse.web.deps import get_data_service

    _login(client, monkeypatch)
    fake = MagicMock()
    fake.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake.get_history.return_value = []
    client.app.dependency_overrides[get_data_service] = lambda: fake

    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0))
    db_session.commit()
    with patch("marketpulse.web.routes.holdings.backfill_holding_sectors", return_value=0):
        r = client.get("/holdings")
    assert "/holdings/AAPL\"" not in r.text
    assert re.search(r'hx-delete="/holdings/\d+"', r.text)
    client.app.dependency_overrides.clear()


def test_holdings_table_tfoot_totals(client, monkeypatch, db_session):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock, patch

    from marketpulse.data.types import Quote
    from marketpulse.db.models import Holding
    from marketpulse.web.deps import get_data_service

    _login(client, monkeypatch)
    fake = MagicMock()
    fake.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake.get_history.return_value = []
    client.app.dependency_overrides[get_data_service] = lambda: fake

    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0))
    db_session.commit()
    with patch("marketpulse.web.routes.holdings.backfill_holding_sectors", return_value=0):
        r = client.get("/holdings")
    assert "<tfoot>" in r.text
    assert "合计" in r.text
    client.app.dependency_overrides.clear()


def test_holdings_monthly_card_renders(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings")
    assert "月度已实现盈亏" in r.text
    assert "mp-monthly-bars" in r.text or "暂无已实现盈亏数据" in r.text


def test_this_month_dividends_zero_when_no_current_month_data(client, monkeypatch, db_session):
    """KPI 含本月 must show 0.00 when current month has NO dividends,
    even if prior months have data."""
    from datetime import UTC, date, datetime
    from unittest.mock import MagicMock, patch

    from marketpulse.data.types import Quote
    from marketpulse.db.models import Dividend, Holding
    from marketpulse.web.deps import get_data_service

    _login(client, monkeypatch)
    fake = MagicMock()
    fake.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake.get_history.return_value = []
    client.app.dependency_overrides[get_data_service] = lambda: fake

    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0, sector="Technology"))
    # Dividend in a PAST month (Jan 2020) — current month has none.
    db_session.add(Dividend(
        ticker="AAPL", ex_date=date(2020, 1, 15),
        amount_per_share=1.0, total_amount=10.0,
    ))
    db_session.commit()

    with patch("marketpulse.web.routes.holdings.backfill_holding_sectors", return_value=0):
        r = client.get("/holdings")
    # Must show 含本月 $0.00, NOT 含本月 $10.00
    assert "含本月 $0.00" in r.text
    client.app.dependency_overrides.clear()


def test_holdings_monthly_card_zero_axis_split(client, monkeypatch, db_session):
    """Holdings monthly card uses same zero-axis split pattern."""
    from datetime import UTC, datetime
    from unittest.mock import MagicMock, patch

    from marketpulse.data.types import Quote
    from marketpulse.db.models import Trade
    from marketpulse.web.deps import get_data_service

    _login(client, monkeypatch)
    fake = MagicMock()
    fake.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake.get_history.return_value = []
    client.app.dependency_overrides[get_data_service] = lambda: fake

    db_session.add_all([
        Trade(ticker="AAPL", action="buy",  quantity=10, price=100,
              fees=0, executed_at=datetime(2026, 1, 1, tzinfo=UTC), realized_pl=None),
        Trade(ticker="AAPL", action="sell", quantity=10, price=120,
              fees=0, executed_at=datetime(2026, 2, 1, tzinfo=UTC), realized_pl=200.0),
        Trade(ticker="AAPL", action="buy",  quantity=10, price=100,
              fees=0, executed_at=datetime(2026, 3, 1, tzinfo=UTC), realized_pl=None),
        Trade(ticker="AAPL", action="sell", quantity=10, price=80,
              fees=0, executed_at=datetime(2026, 4, 1, tzinfo=UTC), realized_pl=-200.0),
    ])
    db_session.commit()
    with patch("marketpulse.web.routes.holdings.backfill_holding_sectors", return_value=0):
        r = client.get("/holdings")
    assert "mp-monthly-bar__bar--pos" in r.text
    assert "mp-monthly-bar__bar--neg" in r.text
    client.app.dependency_overrides.clear()
