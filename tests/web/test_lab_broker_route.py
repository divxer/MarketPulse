"""Tests for /lab/broker — Phase 7a+ Broker Truth Viewer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from marketpulse.auth.password import hash_password
from marketpulse.db import base as db_base
from marketpulse.db.models import (
    BrokerAccountSnapshot,
    BrokerCashSnapshot,
    BrokerPositionSnapshot,
    BrokerSyncRun,
)


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed_completed_run(account_id: str = "DU123") -> int:
    gen = db_base.session_scope()
    db = next(gen)
    try:
        started = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
        run = BrokerSyncRun(
            started_at=started,
            completed_at=started + timedelta(seconds=10),
            broker="IBKR",
            broker_environment="paper",
            account_id=account_id,
            status="completed",
            context={"reference_code": "REF-OK"},
        )
        db.add(run)
        db.flush()
        common = {
            "sync_run_id": run.id,
            "account_id": account_id,
            "broker_environment": "paper",
            "captured_at": run.completed_at,
        }
        db.add(
            BrokerAccountSnapshot(
                **common,
                account_type="INDIVIDUAL",
                base_currency="USD",
                net_liquidation=Decimal("100000.00"),
                buying_power=Decimal("50000.00"),
                maintenance_margin=Decimal("1000.00"),
                excess_liquidity=Decimal("49000.00"),
            )
        )
        db.add(
            BrokerCashSnapshot(
                **common,
                currency="USD",
                cash_balance=Decimal("1000"),
                settled_cash=Decimal("900"),
                accrued_interest=Decimal("0"),
            )
        )
        db.add(
            BrokerPositionSnapshot(
                **common,
                symbol="AAPL",
                asset_class="STK",
                quantity=Decimal("10"),
                avg_cost=Decimal("150"),
                market_price=Decimal("180"),
                market_value=Decimal("1800"),
                unrealized_pnl=Decimal("300"),
                realized_pnl=Decimal("0"),
            )
        )
        db.commit()
        return run.id
    finally:
        db.close()


def _seed_failed_run(*, after: datetime, error_type: str = "TimeoutError") -> int:
    gen = db_base.session_scope()
    db = next(gen)
    try:
        run = BrokerSyncRun(
            started_at=after,
            completed_at=after + timedelta(seconds=5),
            broker="IBKR",
            broker_environment="paper",
            account_id=None,
            status="failed",
            error_type=error_type,
            error_message="boom",
            context={},
        )
        db.add(run)
        db.commit()
        return run.id
    finally:
        db.close()


def test_lab_broker_requires_auth(client):
    response = client.get("/lab/broker", follow_redirects=False)
    # The app redirects unauthenticated HTML to /login (303).
    assert response.status_code == 303


def test_lab_broker_no_data(client, monkeypatch):
    _login(client, monkeypatch)
    response = client.get("/lab/broker")
    assert response.status_code == 200
    assert "No completed broker sync yet" in response.text
    assert "Broker Truth" in response.text


def test_lab_broker_renders_snapshot(client, monkeypatch):
    _login(client, monkeypatch)
    _seed_completed_run(account_id="DU999")
    response = client.get("/lab/broker")
    assert response.status_code == 200
    assert "DU999" in response.text
    assert "AAPL" in response.text
    assert "Latest sync OK" in response.text
    assert "Phase 7a+" in response.text


def test_lab_broker_failed_latest_shows_fallback(client, monkeypatch):
    _login(client, monkeypatch)
    _seed_completed_run(account_id="DU777")
    _seed_failed_run(
        after=datetime(2026, 5, 23, 21, 30, tzinfo=UTC),
        error_type="FlexReportTimeoutError",
    )
    response = client.get("/lab/broker")
    assert response.status_code == 200
    assert "Snapshot from previous completed sync" in response.text
    assert "Latest sync failed" in response.text
    assert "FlexReportTimeoutError" in response.text
    # snapshot data still shows
    assert "DU777" in response.text or "AAPL" in response.text


def test_lab_broker_failed_latest_no_completed(client, monkeypatch):
    _login(client, monkeypatch)
    _seed_failed_run(
        after=datetime(2026, 5, 23, 21, 30, tzinfo=UTC),
        error_type="AuthError",
    )
    response = client.get("/lab/broker")
    assert response.status_code == 200
    assert "No snapshot state" in response.text
    assert "AuthError" in response.text
