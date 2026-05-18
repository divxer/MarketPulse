"""End-to-end RecapService.generate() with v4 prompt parser."""
import json
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from marketpulse.recap.service import RecapService


def _service_with_fakes(db_session, ai_output: str):
    """Create RecapService with fake data + ai services."""
    from marketpulse.data.types import Bar, Quote
    from marketpulse.db.models import WatchlistItem

    # Seed a watchlist item so the service calls ai.daily_commentary
    if not db_session.query(WatchlistItem).filter_by(ticker="SPY").first():
        db_session.add(WatchlistItem(ticker="SPY", sort_order=0))
        db_session.commit()

    spy_quote = Quote(
        ticker="SPY", price=500.0, change_pct=0.24,
        volume=1000, avg_volume_20d=2000,
        fetched_at=datetime.now(UTC), stale=False,
    )
    spy_bars = [
        Bar(date=date(2026, 5, i + 1), open=490.0, high=502.0,
            low=488.0, close=500.0, volume=1000)
        for i in range(30)
    ]

    fake_data = MagicMock()
    from marketpulse.data.types import IndexQuote, MarketOverview
    vix = IndexQuote(symbol="VIX", price=14.18, change_pct=-2.88)
    idx = IndexQuote(symbol="SPY", price=500.0, change_pct=0.24)
    market = MarketOverview(
        spy=idx, qqq=idx, dia=idx, vix=vix,
        fetched_at=datetime.now(UTC),
    )
    fake_data.get_market_overview.return_value = market
    fake_data.get_quote.return_value = spy_quote
    fake_data.get_history.return_value = spy_bars
    fake_data.get_news.return_value = []

    fake_ai = MagicMock()
    fake_ai.daily_commentary.return_value = ai_output

    return RecapService(session=db_session, data=fake_data, ai=fake_ai)


def test_generate_saves_commentary_and_key_events_separately(db_session):
    """Happy path: AI returns Markdown + KEY_EVENTS_JSON → both saved."""
    ai_out = (
        "## 大盘\n\n标普收 `5,973`.\n\n"
        "## 板块\n\nNVDA 回吐.\n\n"
        "---\n\n"
        "KEY_EVENTS_JSON: [{\"time\": \"16:00\", \"title\": \"AVGO 利好\", \"kind\": \"deal\"}]"
    )
    svc = _service_with_fakes(db_session, ai_out)
    result = svc.generate(date(2026, 5, 12))

    assert result.generation_status == "success"
    assert "## 大盘" in result.ai_commentary_text
    assert "KEY_EVENTS_JSON" not in result.ai_commentary_text
    assert result.key_events_json is not None
    events = json.loads(result.key_events_json)
    assert events[0]["title"] == "AVGO 利好"


def test_generate_falls_back_when_no_marker(db_session):
    """No KEY_EVENTS_JSON marker → entire output is commentary, events=NULL."""
    ai_out = "## 大盘\n\n这是一段没有 events 标记的复盘。"
    svc = _service_with_fakes(db_session, ai_out)
    result = svc.generate(date(2026, 5, 12))

    assert result.ai_commentary_text == ai_out
    assert result.key_events_json is None


def test_generate_retry_clears_key_events(db_session):
    """Retry on a previously-failed-parse recap should null out stale events."""
    ai_out_1 = (
        "## 大盘\n\n正文 1\n\n"
        "KEY_EVENTS_JSON: [{\"time\": \"10:00\", \"title\": \"first\", \"kind\": \"deal\"}]"
    )
    svc_1 = _service_with_fakes(db_session, ai_out_1)
    svc_1.generate(date(2026, 5, 12))

    ai_out_2 = "## 大盘\n\n正文 2 没有 events 标记"
    svc_2 = _service_with_fakes(db_session, ai_out_2)
    result = svc_2.generate(date(2026, 5, 12))

    assert "正文 2" in result.ai_commentary_text
    assert result.key_events_json is None  # cleared on retry
