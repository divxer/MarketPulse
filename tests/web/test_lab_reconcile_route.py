"""Phase 7c - /lab/reconcile route."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from marketpulse.auth.password import hash_password
from marketpulse.db import base as db_base
from marketpulse.db.models import (
    BrokerPositionSnapshot,
    BrokerSyncRun,
    PaperOrder,
    PaperPosition,
)


def _login(client, monkeypatch):
    password = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(password))
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    client.post("/login", data={"password": password})


def _seed_completed_with_position(
    *,
    account_id: str = "DU-A",
    broker_quantity: Decimal = Decimal("100"),
    paper_quantity: int = 100,
    broker_started_at: datetime | None = None,
) -> None:
    gen = db_base.session_scope()
    db = next(gen)
    try:
        base = broker_started_at or datetime.now(UTC) - timedelta(hours=1)
        run = BrokerSyncRun(
            started_at=base,
            completed_at=base + timedelta(seconds=10),
            broker="IBKR",
            broker_environment="paper",
            account_id=account_id,
            status="completed",
            context={"reference_code": "REF-OK"},
        )
        db.add(run)
        db.flush()
        db.add(
            BrokerPositionSnapshot(
                sync_run_id=run.id,
                account_id=account_id,
                broker_environment="paper",
                captured_at=run.completed_at,
                symbol="AAPL",
                asset_class="STK",
                quantity=broker_quantity,
            )
        )
        order = PaperOrder(
            idempotency_key=f"reconcile-{broker_quantity}-{paper_quantity}"[:32],
            allocation_run_id="r",
            strategy="general",
            ticker="AAPL",
            quantity=paper_quantity,
            event_time=base,
            allocation_date=date(2026, 5, 25),
            horizon_date=date(2026, 6, 1),
            placed_at=base,
            filled_at=base,
            event_price=Decimal("100"),
            status="ENTRY_FILLED",
            strategy_version="v1",
            allocator_version="v1",
            execution_engine_version="v1",
            weight=1.0,
        )
        db.add(order)
        db.flush()
        db.add(
            PaperPosition(
                order_id=order.id,
                entry_fill_id=1,
                exit_fill_id=None,
                strategy="general",
                ticker="AAPL",
                quantity=paper_quantity,
                entry_price=Decimal("100"),
                entry_date=date(2026, 5, 25),
                horizon_date=date(2026, 6, 1),
                status="OPEN",
                opened_at=base,
            )
        )
        db.commit()
    finally:
        db.close()


def test_requires_auth(client):
    response = client.get("/lab/reconcile", follow_redirects=False)
    assert response.status_code == 303


def test_no_data_state(client, monkeypatch):
    _login(client, monkeypatch)
    response = client.get("/lab/reconcile")
    assert response.status_code == 200
    assert "对账" in response.text
    assert "无法对账" in response.text


def test_matched_state_green(client, monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    _login(client, monkeypatch)
    _seed_completed_with_position()

    response = client.get("/lab/reconcile")
    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "已对齐" in response.text
    get_settings.cache_clear()


def test_summary_cards_render_counts(client, monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    _login(client, monkeypatch)
    _seed_completed_with_position()

    response = client.get("/lab/reconcile")
    assert "已对齐" in response.text
    assert "缺 broker" in response.text
    assert "缺 paper" in response.text
    assert "数量不一致" in response.text
    assert "方向相反" in response.text
    get_settings.cache_clear()


def test_diff_table_renders_columns(client, monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    _login(client, monkeypatch)
    _seed_completed_with_position()

    response = client.get("/lab/reconcile")
    assert "Symbol" in response.text
    assert "Paper" in response.text
    assert "Broker" in response.text
    assert "AAPL" in response.text
    get_settings.cache_clear()


def test_stale_banner_shows_when_broker_old(client, monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    _login(client, monkeypatch)
    _seed_completed_with_position(broker_started_at=datetime.now(UTC) - timedelta(hours=25))

    response = client.get("/lab/reconcile")
    assert "未更新" in response.text or "stale" in response.text.lower()
    get_settings.cache_clear()


def test_quantity_mismatch_renders_yellow_state(client, monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    _login(client, monkeypatch)
    _seed_completed_with_position(broker_quantity=Decimal("50"), paper_quantity=100)

    response = client.get("/lab/reconcile")
    assert response.status_code == 200
    assert "有偏差" in response.text
    assert "数量不一致" in response.text
    get_settings.cache_clear()


def test_side_mismatch_renders_red_state(client, monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    _login(client, monkeypatch)
    _seed_completed_with_position(broker_quantity=Decimal("-10"), paper_quantity=10)

    response = client.get("/lab/reconcile")
    assert response.status_code == 200
    assert "严重偏差" in response.text
    assert "方向相反" in response.text
    get_settings.cache_clear()


def test_nav_link_present(client, monkeypatch):
    _login(client, monkeypatch)
    response = client.get("/lab/reconcile")
    assert 'href="/lab/reconcile"' in response.text
    assert "对账" in response.text
