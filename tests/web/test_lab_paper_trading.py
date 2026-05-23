"""Tests for /lab/paper-trading operations dashboard."""

from __future__ import annotations

from marketpulse.auth.password import hash_password


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_paper_trading_requires_auth(client):
    response = client.get("/lab/paper-trading", follow_redirects=False)
    assert response.status_code == 303


def test_paper_trading_post_not_registered(client, monkeypatch):
    _login(client, monkeypatch)
    response = client.post("/lab/paper-trading")
    assert response.status_code == 405


def test_paper_trading_fresh_db_renders_empty_healthy_page(client, monkeypatch):
    _login(client, monkeypatch)
    response = client.get("/lab/paper-trading")

    assert response.status_code == 200
    assert "Paper Trading · Operations" in response.text
    assert "System Status" in response.text
    assert "Healthy" in response.text
    assert "Generated at" in response.text
    assert "No paper tick has completed yet" in response.text
    assert "No open paper positions" in response.text
    assert "No operational events in current cycle" in response.text
    assert "No order lifecycle activity in current cycle" in response.text
    assert "纸上交易" in response.text


def test_paper_trading_has_no_control_plane_controls(client, monkeypatch):
    _login(client, monkeypatch)
    response = client.get("/lab/paper-trading")

    assert "Force Close" not in response.text
    assert "Replay" not in response.text
    assert "Retry" not in response.text
    assert "Kill Switch Toggle" not in response.text
    assert 'type="submit"' not in response.text


def test_paper_trading_renders_degraded_positions_section(client, monkeypatch):
    _login(client, monkeypatch)

    import marketpulse.trading.query_models as qm

    def fail_positions(db, window, today, rows):
        raise RuntimeError("boom")

    monkeypatch.setattr(qm, "_load_positions_section", fail_positions)
    response = client.get("/lab/paper-trading")

    assert response.status_code == 200
    assert "Degraded" in response.text
    assert "Unable to load Positions" in response.text
    assert "Traceback" not in response.text
