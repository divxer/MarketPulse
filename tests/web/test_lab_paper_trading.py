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
    assert "RuntimeError" in response.text
    assert "Traceback" not in response.text


def test_paper_trading_renders_degraded_dashboard_for_shared_query_failure(
    client,
    monkeypatch,
):
    _login(client, monkeypatch)

    import marketpulse.trading.query_models as qm

    def fail_window(db):
        raise RuntimeError("window failed")

    monkeypatch.setattr(qm, "_load_operational_window", fail_window)
    response = client.get("/lab/paper-trading")

    assert response.status_code == 200
    assert "Degraded" in response.text
    assert "Unable to load dashboard data" in response.text
    assert "Unable to load Critical Events" in response.text
    assert "RuntimeError" in response.text
    assert "Traceback" not in response.text


def test_paper_trading_renders_zero_pnl_values(client, monkeypatch):
    from datetime import UTC, datetime
    from decimal import Decimal

    _login(client, monkeypatch)

    import marketpulse.trading.query_models as qm

    generated_at = datetime(2026, 5, 23, 21, 34, tzinfo=UTC)
    dashboard = qm.PaperTradingDashboard(
        generated_at=generated_at,
        generated_at_label="Generated at 17:34 NY",
        current_operational_window=qm.OperationalWindow(
            started_at=generated_at,
            source_event_type="TICK_COMPLETED",
            label="Operational Window · Started 2026-05-23 17:34 NY",
        ),
        system_status="Healthy",
        health=qm.HealthSummary(
            cash_balance=Decimal("1000.000000"),
            realized_pnl_today=Decimal("0.000000"),
            open_positions_count=0,
            latest_tick_status="completed",
            kill_switch_state="OFF",
            kill_switch_reason=None,
        ),
        critical_events=qm.section_ok([], "No operational events in current cycle"),
        positions=qm.section_ok(
            [
                qm.PositionRow(
                    position_id=1,
                    order_id=1,
                    ticker="AAPL",
                    strategy="momentum_breakout",
                    quantity=3,
                    entry_price=Decimal("100.000000"),
                    entry_date=generated_at.date(),
                    horizon_date=generated_at.date(),
                    canonical_status="CLOSED",
                    operational_exit_status="CLOSED",
                    exit_health_label="Closed",
                    realized_pnl=Decimal("0.000000"),
                ),
            ],
            "No open paper positions",
        ),
        order_lifecycles=qm.section_ok(
            [
                qm.OrderLifecycleRow(
                    order_id=1,
                    ticker="AAPL",
                    strategy="momentum_breakout",
                    quantity=3,
                    order_status="EXIT_FILLED",
                    placed_at=generated_at,
                    entry_price=Decimal("100.000000"),
                    entry_time=generated_at,
                    exit_price=Decimal("100.000000"),
                    exit_time=generated_at,
                    realized_pnl=Decimal("0.000000"),
                    latest_audit_reason="closed",
                ),
            ],
            "No order lifecycle activity in current cycle",
        ),
        audit_timeline=qm.section_ok(
            qm.AuditTimeline(rows=[], routine_hidden_count=0),
            "No operational events in current cycle",
        ),
        closed_trades=qm.section_ok(
            qm.ClosedTrades(
                summary=qm.ClosedTradesSummary(
                    total_count=0,
                    realized_pnl_total=Decimal("0.000000"),
                    win_rate=None,
                    avg_return_pct=None,
                ),
                rows=[],
                count_label="Showing 0 closed trades",
            ),
            "No closed trades yet",
        ),
    )
    monkeypatch.setattr(
        qm,
        "load_paper_trading_dashboard",
        lambda db: dashboard,
    )

    response = client.get("/lab/paper-trading")

    assert response.status_code == 200
    assert "0.000000" in response.text


def test_paper_trading_uses_compact_ops_console_layout(client, monkeypatch):
    _login(client, monkeypatch)
    response = client.get("/lab/paper-trading")

    assert "max-w-[2400px]" in response.text
    assert "mp-paper-ops" in response.text
    assert "mp-paper-kpis" in response.text
    assert "mp-paper-primary-row" in response.text
    assert response.text.index("Critical Events") < response.text.index(
        "Orders &amp; Fills",
    )
    assert response.text.index("Positions") < response.text.index("Audit Timeline")


def test_paper_trading_css_reserves_stable_tab_width():
    css = "marketpulse/web/static/css/app.css"
    text = __import__("pathlib").Path(css).read_text()

    assert "scrollbar-gutter: stable;" in text
    assert ".mp-paper-tab-panel" in text
    assert "min-width: 0;" in text


def test_paper_trading_renders_closed_trades(client, monkeypatch, db_url):
    from datetime import UTC, date, datetime
    from decimal import Decimal

    _login(client, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", db_url)
    from marketpulse.config import get_settings

    get_settings.cache_clear()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from marketpulse.db.models import PaperOrder, PaperPosition

    engine = create_engine(db_url)
    with Session(engine) as s:
        order = PaperOrder(
            idempotency_key="ct-key-1",
            allocation_run_id="run-1",
            strategy="general",
            ticker="ZQK",
            quantity=3,
            event_time=datetime(2026, 6, 2, 21, 30, tzinfo=UTC),
            allocation_date=date(2026, 5, 28),
            horizon_date=date(2026, 6, 2),
            placed_at=datetime(2026, 6, 2, 21, 30, tzinfo=UTC),
            filled_at=datetime(2026, 6, 2, 21, 30, tzinfo=UTC),
            event_price=Decimal("100"),
            status="ENTRY_FILLED",
            strategy_version="v1",
            allocator_version="v1",
            execution_engine_version="v1",
            weight=1.0,
            contribution_multiplier=1.0,
            effective_corr_window=60,
            rewarded_for_negative_corr=False,
            would_change_rank=False,
            size_clamped_by_override=False,
        )
        s.add(order)
        s.flush()
        s.add(
            PaperPosition(
                order_id=order.id,
                entry_fill_id=1,
                exit_fill_id=2,
                strategy="general",
                ticker="ZQK",
                quantity=3,
                entry_price=Decimal("100"),
                entry_date=date(2026, 5, 28),
                horizon_date=date(2026, 6, 2),
                status="CLOSED",
                opened_at=datetime(2026, 6, 2, 21, 30, tzinfo=UTC),
                closed_at=datetime(2026, 6, 2, 21, 30, tzinfo=UTC),
                exit_price=Decimal("110"),
                realized_pnl=Decimal("30"),
            )
        )
        s.commit()

    r = client.get("/lab/paper-trading")
    assert r.status_code == 200
    body = r.text
    assert "Closed Trades" in body
    assert "ZQK" in body
    assert "Showing 1 closed trades" in body


def test_paper_trading_closed_trades_empty_state(client, monkeypatch, db_url):
    _login(client, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", db_url)
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    r = client.get("/lab/paper-trading")
    assert r.status_code == 200
    assert "Closed Trades" in r.text
    assert "No closed trades yet" in r.text
