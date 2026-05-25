"""RecapService.generate() records EvaluationEvent per VERDICTS_JSON entry."""
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from marketpulse.db.models import EvaluationEvent, WatchlistItem
from marketpulse.recap.service import RecapService


def _build_service(db_session, ai_output: str):
    from datetime import date as _date

    from marketpulse.data.types import Bar, Quote

    db_session.add(WatchlistItem(ticker="AAPL", sort_order=0))
    db_session.commit()

    fake_data = MagicMock()
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake_data.get_history.return_value = [
        Bar(date=_date(2026, 5, d), open=180, high=181, low=179,
            close=180.0, volume=1000)
        for d in range(1, 16)
    ]
    fake_data.get_news.return_value = []

    # Market overview
    market = MagicMock()
    spy = Quote(ticker="SPY", price=500.0, change_pct=0.5,
                volume=0, avg_volume_20d=0, fetched_at=datetime.now(UTC), stale=False)
    market.spy = market.qqq = market.dia = spy
    market.vix = Quote(ticker="VIX", price=14.0, change_pct=-1.0,
                       volume=0, avg_volume_20d=0,
                       fetched_at=datetime.now(UTC), stale=False)
    fake_data.get_market_overview.return_value = market

    fake_ai = MagicMock()
    fake_ai.daily_commentary.return_value = ai_output

    return RecapService(session=db_session, data=fake_data, ai=fake_ai)


_AI_OUTPUT_3_VERDICTS = (
    "## 大盘\n\n正文\n\n"
    "KEY_EVENTS_JSON: []\n\n"
    "VERDICTS_JSON: ["
    "{\"ticker\": \"AAPL\", \"verdict\": \"bullish\", \"rationale\": \"a\"},"
    "{\"ticker\": \"NVDA\", \"verdict\": \"bearish\", \"rationale\": \"b\"},"
    "{\"ticker\": \"GOOGL\", \"verdict\": \"neutral\", \"rationale\": \"c\"}"
    "]"
)


def test_recap_with_3_verdicts_records_3_events(db_session):
    svc = _build_service(db_session, _AI_OUTPUT_3_VERDICTS)
    svc.generate(date(2026, 5, 15))
    events = db_session.query(EvaluationEvent).all()
    assert len(events) == 3
    tickers = sorted(e.ticker for e in events)
    assert tickers == ["AAPL", "GOOGL", "NVDA"]
    for e in events:
        assert e.payload["source"] == "recap"
        assert e.payload["recap_date"] == "2026-05-15"
        # Phase 7c follow-up: recap events MUST carry strategy so the
        # nightly paper_trading_tick BidAggregator collects them (it
        # skips events with empty payload.strategy).
        assert e.payload["strategy"] == "general"
        assert e.payload["strategy_version"] == "v1"


def test_recap_without_verdicts_marker_no_events(db_session):
    raw = "## 大盘\n\n没有 verdicts.\n\nKEY_EVENTS_JSON: []"
    svc = _build_service(db_session, raw)
    svc.generate(date(2026, 5, 15))
    assert db_session.query(EvaluationEvent).count() == 0


def test_recap_retry_deletes_old_events_for_same_date(db_session):
    svc = _build_service(db_session, _AI_OUTPUT_3_VERDICTS)
    svc.generate(date(2026, 5, 15))   # 3 events
    # Now retry with a different verdict set
    raw_2 = (
        "## 大盘\n\n正文 2\n\n"
        "VERDICTS_JSON: [{\"ticker\": \"TSLA\", \"verdict\": \"bullish\", \"rationale\": \"x\"}]"
    )
    svc.ai.daily_commentary.return_value = raw_2
    svc.generate(date(2026, 5, 15))
    events = db_session.query(EvaluationEvent).all()
    # Old 3 deleted; only the new 1 remains
    assert len(events) == 1
    assert events[0].ticker == "TSLA"


def test_recap_with_mixed_valid_invalid_verdicts_skips_invalid(db_session):
    raw = (
        "## 大盘\n\n正文\n\n"
        "VERDICTS_JSON: ["
        "{\"ticker\": \"AAPL\", \"verdict\": \"bullish\", \"rationale\": \"a\"},"
        "{\"ticker\": \"NVDA\", \"verdict\": \"moon\", \"rationale\": \"b\"},"
        "{\"verdict\": \"bearish\"}"
        "]"
    )
    svc = _build_service(db_session, raw)
    svc.generate(date(2026, 5, 15))
    # Only AAPL recorded; NVDA dropped (invalid verdict); third dropped (missing ticker)
    events = db_session.query(EvaluationEvent).all()
    assert len(events) == 1
    assert events[0].ticker == "AAPL"


def test_recap_verdict_skipped_when_quote_fetch_fails(db_session):
    raw = (
        "## 大盘\n\n正文\n\n"
        "VERDICTS_JSON: ["
        "{\"ticker\": \"AAPL\", \"verdict\": \"bullish\", \"rationale\": \"a\"},"
        "{\"ticker\": \"NVDA\", \"verdict\": \"bearish\", \"rationale\": \"b\"}"
        "]"
    )
    svc = _build_service(db_session, raw)
    # NVDA quote fetch fails; AAPL succeeds
    def quote_side_effect(t):
        from marketpulse.data.types import Quote
        if t == "NVDA":
            raise RuntimeError("yfinance down")
        return Quote(ticker=t, price=180.0, change_pct=1.0, volume=1000,
                    avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False)
    svc.data.get_quote.side_effect = quote_side_effect
    svc.generate(date(2026, 5, 15))
    events = db_session.query(EvaluationEvent).all()
    assert len(events) == 1
    assert events[0].ticker == "AAPL"


def test_recap_duplicate_ticker_in_verdicts_records_both(db_session):
    """Spec doesn't dedupe — AI repeating ticker produces 2 events."""
    raw = (
        "## 大盘\n\n正文\n\n"
        "VERDICTS_JSON: ["
        "{\"ticker\": \"AAPL\", \"verdict\": \"bullish\", \"rationale\": \"first\"},"
        "{\"ticker\": \"AAPL\", \"verdict\": \"neutral\", \"rationale\": \"second\"}"
        "]"
    )
    svc = _build_service(db_session, raw)
    svc.generate(date(2026, 5, 15))
    events = db_session.query(EvaluationEvent).all()
    assert len(events) == 2
