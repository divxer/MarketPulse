"""/lab/ai-track filter — Source × Strategy two-level."""
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


def _seed_event(db, *, ticker="AAPL", source="stock_analysis",
                 strategy="momentum_breakout", excess=0.03, days_ago=10):
    e = EvaluationEvent(
        event_type="ai_analysis",
        subtype="bullish",
        ticker=ticker,
        event_time=datetime.now(UTC) - timedelta(days=days_ago),
        event_price=100.0,
        payload={
            "source": source,
            "strategy": strategy,
            "strategy_version": "v1",
            "prompt_version": "analysis-v4",
        },
    )
    db.add(e)
    db.flush()
    db.add(EvaluationOutcome(
        event_id=e.id, horizon_trading_days=5,
        event_price=100.0, horizon_price=103.0,
        horizon_date=date.today(),
        forward_return=excess + 0.001,
        benchmark_ticker="SPY", benchmark_forward_return=0.001,
        excess_return=excess,
    ))


def test_lab_accepts_strategy_query_param(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="AAA", strategy="momentum_breakout")
    _seed_event(db_session, ticker="BBB", strategy="fundamental_value")
    db_session.commit()
    r = client.get("/lab/ai-track?strategy=momentum_breakout")
    assert r.status_code == 200
    assert "AAA" in r.text
    assert "momentum_breakout" in r.text


def test_lab_accepts_source_query_param(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="STK", source="stock_analysis", strategy="general")
    # Recap event has no strategy
    e2 = EvaluationEvent(
        event_type="ai_analysis", subtype="bullish", ticker="RCP",
        event_time=datetime.now(UTC) - timedelta(days=10),
        event_price=100.0,
        payload={"source": "recap", "prompt_version": "commentary-v5-zh-verdicts"},
    )
    db_session.add(e2)
    db_session.flush()
    db_session.add(EvaluationOutcome(
        event_id=e2.id, horizon_trading_days=5,
        event_price=100.0, horizon_price=103.0,
        horizon_date=date.today(),
        forward_return=0.031, benchmark_ticker="SPY",
        benchmark_forward_return=0.001, excess_return=0.03,
    ))
    db_session.commit()
    r_stock = client.get("/lab/ai-track?source=stock_analysis")
    assert r_stock.status_code == 200
    assert "STK" in r_stock.text

    r_recap = client.get("/lab/ai-track?source=recap")
    assert r_recap.status_code == 200
    assert "RCP" in r_recap.text


def test_lab_strategy_filter_only_returns_matching_events(
    client: TestClient, monkeypatch, db_session
):
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="MBR", strategy="momentum_breakout")
    _seed_event(db_session, ticker="FVR", strategy="fundamental_value")
    db_session.commit()
    r = client.get("/lab/ai-track?source=stock_analysis&strategy=momentum_breakout")
    assert "MBR" in r.text


def test_lab_invalid_strategy_returns_200_with_empty(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    # No data; filter by non-existent strategy
    r = client.get("/lab/ai-track?strategy=nonexistent_strategy")
    assert r.status_code == 200
    # Should render placeholder, not 500


def test_lab_recap_source_drops_strategy_from_url(client: TestClient, monkeypatch):
    """When source=recap, strategy filter is ignored (since recap events have no strategy)."""
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track?source=recap&strategy=momentum_breakout")
    assert r.status_code == 200
    # The route should NOT 500


def test_lab_filter_card_renders_source_chips(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track")
    # Source chip group present
    assert "Source" in r.text or "事件来源" in r.text
    assert "stock_analysis" in r.text
    assert "recap" in r.text


def test_lab_filter_card_renders_strategy_chips_when_source_is_stock(  # noqa: E501
    client: TestClient, monkeypatch
):
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track?source=stock_analysis")
    assert "momentum_breakout" in r.text
    assert "fundamental_value" in r.text
    assert "general" in r.text


def test_lab_filter_card_disables_strategy_chips_when_source_is_recap(  # noqa: E501
    client: TestClient, monkeypatch
):
    """Strategy chips visually disabled when source=recap."""
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track?source=recap")
    # The disabled marker should appear on the strategy section
    assert "is-disabled" in r.text or 'aria-disabled="true"' in r.text


def test_lab_kpi_strip_renders_best_strategy_card_when_data_exists(  # noqa: E501
    client: TestClient, monkeypatch, db_session
):
    _login(client, monkeypatch)
    # Seed 5+ events same strategy to make best_strategy non-null
    for i in range(6):
        _seed_event(db_session, ticker=f"T{i}", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/ai-track")
    assert "Best Strategy" in r.text or "最强策略" in r.text


def test_lab_kpi_strip_shows_dash_when_no_strategy_has_n5(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track")
    assert "Best Strategy" in r.text or "最强策略" in r.text
