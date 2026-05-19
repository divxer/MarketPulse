"""Tests for /lab/backtest route."""
from datetime import UTC, date, datetime, timedelta

from marketpulse.auth.password import hash_password
from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed_event(db, *, ticker, strategy, excess=0.03, days_ago=10, horizon=5):
    e = EvaluationEvent(
        event_type="ai_analysis", subtype="bullish", ticker=ticker,
        event_time=datetime.now(UTC) - timedelta(days=days_ago),
        event_price=100.0,
        payload={"source": "stock_analysis", "strategy": strategy,
                 "strategy_version": "v1", "prompt_version": "analysis-v4"},
    )
    db.add(e)
    db.flush()
    db.add(EvaluationOutcome(
        event_id=e.id, horizon_trading_days=horizon,
        event_price=100.0, horizon_price=100 * (1 + excess + 0.001),
        horizon_date=date.today() - timedelta(days=max(0, days_ago - horizon)),
        forward_return=excess + 0.001, benchmark_ticker="SPY",
        benchmark_forward_return=0.001, excess_return=excess,
    ))


def test_lab_backtest_renders_with_no_data(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/backtest")
    assert r.status_code == 200
    assert "linear_interpolation_v0" in r.text
    assert "research" in r.text.lower() or "研究" in r.text


def test_lab_backtest_invalid_horizon_returns_422(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/backtest?horizon=3")
    assert r.status_code == 422


def test_lab_backtest_accepts_horizon_5(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/backtest?horizon=5")
    assert r.status_code == 200


def test_lab_backtest_renders_strategy_names_when_data_present(
    client, monkeypatch, db_session,
):
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest")
    assert "动量突破" in r.text
    assert "SPY 基准" in r.text


def test_lab_backtest_since_days_all_works(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="OLD", strategy="momentum_breakout", days_ago=200)
    db_session.commit()
    r = client.get("/lab/backtest?since_days=all")
    assert r.status_code == 200
    assert "OLD" in r.text or "动量突破" in r.text


def test_lab_backtest_requires_auth(client):
    """Unauthenticated → 303 redirect to login (like other /lab pages)."""
    r = client.get("/lab/backtest", follow_redirects=False)
    assert r.status_code == 303


def test_lab_backtest_renders_5_kpi_cards(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    for i in range(6):
        _seed_event(db_session, ticker=f"T{i}", strategy="momentum_breakout",
                    excess=0.03)
    db_session.commit()
    r = client.get("/lab/backtest")
    assert "Best Strategy" in r.text
    assert "Best Sharpe" in r.text
    assert "Best Cum Ret" in r.text or "Best Return" in r.text
    assert "Worst MaxDD" in r.text or "MaxDD" in r.text
    assert "vs SPY" in r.text


def test_lab_backtest_kpi_shows_dash_when_no_qualifying_strategy(
    client, monkeypatch, db_session,
):
    _login(client, monkeypatch)
    for i in range(2):
        _seed_event(db_session, ticker=f"T{i}", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest")
    assert "Best Strategy" in r.text
    assert "—" in r.text or "n&lt;5" in r.text or "n<5" in r.text


def test_lab_backtest_renders_equity_curve_svg(
    client, monkeypatch, db_session,
):
    _login(client, monkeypatch)
    for i in range(6):
        _seed_event(db_session, ticker=f"T{i}", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest")
    assert "<svg" in r.text
    assert "<polyline" in r.text
    assert r.text.count("<polyline") >= 2


def test_lab_backtest_renders_drawdown_svg(
    client, monkeypatch, db_session,
):
    _login(client, monkeypatch)
    for i in range(6):
        _seed_event(db_session, ticker=f"T{i}", strategy="momentum_breakout",
                    excess=-0.02)
    db_session.commit()
    r = client.get("/lab/backtest")
    assert "Drawdown" in r.text or "回撤" in r.text
