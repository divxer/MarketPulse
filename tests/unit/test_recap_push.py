import json
from datetime import UTC, date, datetime

from marketpulse.db.models import DailyRecap
from marketpulse.recap.push import build_summary


def _recap(**overrides) -> DailyRecap:
    r = DailyRecap(
        recap_date=date(2026, 5, 10),
        market_summary_json=json.dumps({"spy": 0.45, "qqq": -0.30, "dia": 0.10, "vix": 18.2}),
        watchlist_performance_json=json.dumps([
            {"ticker": "AAPL", "signals": ["EMA_GOLDEN_CROSS"]},
            {"ticker": "NVDA", "signals": ["RSI_OVERBOUGHT"]},
            {"ticker": "TSLA", "signals": []},
        ]),
        holdings_overview_json=json.dumps([
            {"ticker": "QUBT", "pl_pct": -21.0},
            {"ticker": "TQQQ", "pl_pct": 375.5},
        ]),
        holdings_totals_json=json.dumps({"pl_dollars": -2064.41, "pl_pct": -2.64}),
        ai_commentary_text="今日大盘震荡。持仓中 TQQQ 表现突出,QUBT 续跌需注意止损。" * 10,
        generation_status="success",
        generated_at=datetime.now(UTC),
    )
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


def test_build_summary_has_title_and_body() -> None:
    title, body = build_summary(_recap(), base_url="https://nas.local:8088")
    assert "2026-05-10" in title
    assert "MarketPulse" in title
    assert "📈" in body or "大盘" in body
    assert "持仓" in body
    assert "AAPL" in body
    assert "https://nas.local:8088" in body


def test_build_summary_skips_missing_sections() -> None:
    r = _recap(market_summary_json=None, holdings_overview_json=None, ai_commentary_text=None)
    _, body = build_summary(r)
    # Watchlist signals still rendered
    assert "AAPL" in body
    # Holdings section absent
    assert "持仓" not in body


def test_build_summary_omits_link_when_no_base_url() -> None:
    _, body = build_summary(_recap(), base_url=None)
    assert "详情" not in body


def test_build_summary_truncates_long_ai_commentary() -> None:
    long_text = "测试" * 2500
    _, body = build_summary(_recap(ai_commentary_text=long_text))
    ai_segment = body.split("🤖 AI 总评")[-1].split("───")[0]
    assert len(ai_segment.strip()) < 300


def test_build_summary_truncates_for_bark() -> None:
    huge_watch = json.dumps([
        {"ticker": f"T{i}", "signals": ["EMA_GOLDEN_CROSS"]} for i in range(200)
    ])
    r = _recap(watchlist_performance_json=huge_watch)
    _, body = build_summary(r, notifier_kind="bark")
    assert len(body) <= 4096


def test_build_summary_does_not_truncate_for_smtp() -> None:
    huge_watch = json.dumps([
        {"ticker": f"T{i}", "signals": ["EMA_GOLDEN_CROSS"]} for i in range(500)
    ])
    r = _recap(watchlist_performance_json=huge_watch)
    _, body = build_summary(r, notifier_kind="smtp")
    assert len(body) > 4096
