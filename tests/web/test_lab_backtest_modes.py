"""Phase 5a route — ?mode=per-strategy | shared-pool toggle."""
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


def test_lab_backtest_default_mode_is_per_strategy(client, monkeypatch):
    """No ?mode= → Phase 4 view (backward compat)."""
    _login(client, monkeypatch)
    r = client.get("/lab/backtest")
    assert r.status_code == 200
    assert "Best Strategy" in r.text


def test_lab_backtest_accepts_shared_pool_mode(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/backtest?mode=shared-pool")
    assert r.status_code == 200


def test_lab_backtest_accepts_per_strategy_mode_explicit(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/backtest?mode=per-strategy")
    assert r.status_code == 200


def test_lab_backtest_invalid_mode_returns_422(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/backtest?mode=garbage")
    assert r.status_code == 422


def test_lab_backtest_per_strategy_unchanged_with_shared_data(
    client, monkeypatch, db_session,
):
    """Phase 4 regression."""
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest?mode=per-strategy")
    assert "Best Strategy" in r.text


def test_lab_backtest_shared_mode_renders_size_distribution_context(
    client, monkeypatch, db_session,
):
    """Backend computes size_distribution and passes it via context."""
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest?mode=shared-pool")
    assert r.status_code == 200
    # The SVG sparkline rendering uses this; we don't directly assert the
    # list value, just that the page rendered without error.
    assert "shared" in r.text.lower() or "共享池" in r.text


def test_lab_backtest_shared_mode_includes_sizing_policy_in_hero(
    client, monkeypatch, db_session,
):
    """Hero text includes sizing_policy provenance line in shared mode."""
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest?mode=shared-pool")
    # 2nd hero sentence references the sizing policy
    assert "vol_target_conviction_v0" in r.text


def test_lab_backtest_shared_mode_renders_sector_breakdown_section(
    client, monkeypatch, db_session,
):
    """Shared-pool mode renders the new sector breakdown section."""
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest?mode=shared-pool")
    assert r.status_code == 200
    assert "Sector 暴露分布" in r.text or "sector" in r.text.lower()


def test_lab_backtest_shared_mode_renders_cap_policy_in_hero(
    client, monkeypatch, db_session,
):
    """Hero text includes risk_policy provenance line."""
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest?mode=shared-pool")
    assert "cap40_corr06_enforced_v0" in r.text or "sector_cap_policy" in r.text
