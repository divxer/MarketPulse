"""Strategy chip in the AI analysis block (HTMX swap target).

Phase 3 moved the chip from the page-level header (where it landed
visually outside the AI card) into partials/analysis_block.html — the
fragment rendered by POST /stock/{ticker}/analyze. The chip only shows
WHEN an analysis result is being rendered, not on the bare /stock page.
"""
from datetime import UTC, datetime
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from marketpulse.ai.types import AnalysisResult
from marketpulse.auth.password import hash_password
from marketpulse.data.types import Quote


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


def _override_ai(client, *, strategy: str | None, response_markdown: str = "## Body\n\n正文"):
    """Override get_ai_service to return a fake that yields a fixed result.

    Strategy is what the AnalysisResult carries (post-Phase-3 field).
    """
    from marketpulse.web.deps import get_ai_service

    fake = MagicMock()
    fake.analyze.return_value = AnalysisResult(
        ticker="AAPL",
        model="claude-sonnet-4-6",
        prompt_version="analysis-v4",
        strategy=strategy,
        strategy_version="v1" if strategy else None,
        response_markdown=response_markdown,
        requested_at=datetime.now(UTC),
        cached=False,
    )
    client.app.dependency_overrides[get_ai_service] = lambda: fake


def _set_fake_data(client):
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()


def _clear_overrides(client):
    client.app.dependency_overrides.clear()


def test_analyze_block_renders_strategy_chip_with_display_name(
    client: TestClient, monkeypatch,
):
    """POST /stock/AAPL/analyze → returned fragment contains the chip."""
    _login(client, monkeypatch)
    _set_fake_data(client)
    _override_ai(client, strategy="momentum_breakout")
    try:
        r = client.post("/stock/AAPL/analyze")
        assert r.status_code == 200
        assert "mp-chip--strategy" in r.text
        assert "动量突破" in r.text  # display_name, not snake_case


    finally:
        _clear_overrides(client)


def test_analyze_block_chip_resolves_display_name_not_internal(
    client: TestClient, monkeypatch,
):
    _login(client, monkeypatch)
    _set_fake_data(client)
    _override_ai(client, strategy="fundamental_value")
    try:
        r = client.post("/stock/AAPL/analyze")
        assert r.status_code == 200
        assert "价值分析" in r.text
    finally:
        _clear_overrides(client)


def test_analyze_block_no_chip_when_strategy_is_null(
    client: TestClient, monkeypatch,
):
    """Legacy/cached results without a strategy field don't show the chip."""
    _login(client, monkeypatch)
    _set_fake_data(client)
    _override_ai(client, strategy=None)
    try:
        r = client.post("/stock/AAPL/analyze")
        assert r.status_code == 200
        assert "mp-chip--strategy" not in r.text
    finally:
        _clear_overrides(client)


def test_stock_page_does_not_render_chip_before_analysis_runs(
    client: TestClient, monkeypatch,
):
    """The bare GET /stock/AAPL page has an empty #analysis div — no chip
    until the user clicks AI 分析 and the HTMX swap fires."""
    _login(client, monkeypatch)
    _set_fake_data(client)
    try:
        r = client.get("/stock/AAPL")
        assert r.status_code == 200
        assert "mp-chip--strategy" not in r.text
    finally:
        _clear_overrides(client)
