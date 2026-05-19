"""Strategy leaderboard partial in /lab rail."""
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed(db, *, ticker, strategy, excess, days_ago=10):
    e = EvaluationEvent(
        event_type="ai_analysis", subtype="bullish", ticker=ticker,
        event_time=datetime.now(UTC) - timedelta(days=days_ago),
        event_price=100.0,
        payload={
            "source": "stock_analysis", "strategy": strategy,
            "strategy_version": "v1", "prompt_version": "analysis-v4",
        },
    )
    db.add(e)
    db.flush()
    db.add(EvaluationOutcome(
        event_id=e.id, horizon_trading_days=5,
        event_price=100.0, horizon_price=100 * (1 + excess + 0.001),
        horizon_date=date.today(),
        forward_return=excess + 0.001, benchmark_ticker="SPY",
        benchmark_forward_return=0.001, excess_return=excess,
    ))


def test_lab_strategy_table_renders_when_data_exists(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session, ticker="A1", strategy="momentum_breakout", excess=0.05)
    _seed(db_session, ticker="A2", strategy="fundamental_value", excess=0.02)
    db_session.commit()
    r = client.get("/lab/ai-track")
    assert "按 Strategy" in r.text or "Strategy Leaderboard" in r.text
    # Both strategies appear in the table
    assert "动量突破" in r.text
    assert "价值分析" in r.text


def test_lab_strategy_table_orders_by_hit_rate_desc(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    # momentum: 2/2 hits; fundamental: 0/2 hits
    _seed(db_session, ticker="A1", strategy="momentum_breakout", excess=0.05)
    _seed(db_session, ticker="A2", strategy="momentum_breakout", excess=0.04)
    _seed(db_session, ticker="B1", strategy="fundamental_value", excess=-0.05)
    _seed(db_session, ticker="B2", strategy="fundamental_value", excess=-0.04)
    db_session.commit()
    r = client.get("/lab/ai-track")
    # Scope search to the strategy leaderboard section only (after "按 Strategy" heading)
    leaderboard_start = r.text.index("按 Strategy")
    leaderboard_text = r.text[leaderboard_start:]
    # momentum_breakout (100%) should appear before fundamental_value (0%) in the leaderboard
    mbox = leaderboard_text.index("动量突破")
    fbox = leaderboard_text.index("价值分析")
    assert mbox < fbox


def test_lab_strategy_table_shows_expected_horizons_hint(
    client: TestClient, monkeypatch, db_session
):
    _login(client, monkeypatch)
    _seed(db_session, ticker="X", strategy="momentum_breakout", excess=0.03)
    db_session.commit()
    r = client.get("/lab/ai-track")
    # The "rated for: 5d / 20d" hint
    assert "5d" in r.text  # momentum_breakout's expected_horizons


def test_lab_strategy_table_empty_when_no_data(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track")
    # Render placeholder, no error
    assert r.status_code == 200
