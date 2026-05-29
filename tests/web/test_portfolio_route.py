# Layer: test
"""PR4 — /lab/portfolio-vs-spy route tests."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password


def _login(client: TestClient, monkeypatch) -> None:
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed(session, d: date, *, excess="0.03", sufficient=False):
    from marketpulse.portfolio.north_star import NavSnapshot
    from marketpulse.portfolio.snapshot_repo import insert_snapshot
    insert_snapshot(session, NavSnapshot(
        trading_date=d, cash_balance=Decimal("100000"), holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal("103000"), anchor_portfolio_nav=Decimal("100000"),
        portfolio_index=Decimal("1.03"), spy_close=Decimal("500"),
        anchor_spy_close=Decimal("500"), spy_index=Decimal("1.00"),
        excess_return=Decimal(excess), trading_days_observed=42,
        coverage_ratio=Decimal("0.46"), is_sufficient=sufficient,
        unpriced_positions_count=0, unpriced_tickers=(),
    ))


def test_route_requires_auth(client: TestClient):
    r = client.get("/lab/portfolio-vs-spy", headers={"Accept": "application/json"})
    assert r.status_code == 401


def test_route_empty_db_renders_empty_state(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/portfolio-vs-spy")
    assert r.status_code == 200
    assert "No snapshots yet" in r.text


def test_route_insufficient_shows_banner_and_hero(client, monkeypatch, db_url):
    # The `client` fixture already binds the global engine to `db_url`
    # (same tmp_path/test.db). Seeding via a fresh engine on the SAME url is
    # therefore visible to the route's get_db session — no DATABASE_URL
    # monkeypatch needed.
    _login(client, monkeypatch)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    engine = create_engine(db_url)
    with Session(engine) as s:
        _seed(s, date(2026, 8, 13))
        _seed(s, date(2026, 8, 14), excess="0.032")
        s.commit()

    r = client.get("/lab/portfolio-vs-spy")
    assert r.status_code == 200
    assert "PRELIMINARY" in r.text
    assert "+3.2%" in r.text
    assert "<polyline" in r.text
    assert "Portfolio" in r.text
    assert "SPY" in r.text
    assert "Excess Return" in r.text


def test_nav_contains_all_lab_links(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/portfolio-vs-spy")
    assert r.status_code == 200
    for href in (
        "/lab/portfolio-vs-spy", "/lab/ai-track", "/lab/backtest",
        "/lab/paper-trading", "/lab/broker", "/lab/reconcile",
    ):
        assert f'href="{href}"' in r.text


def test_page_width_matches_lab_group(client, monkeypatch):
    """The page (and its nav) must use the lab-group width max-w-[2400px],
    not base's max-w-5xl — otherwise the frame/nav width jumps when switching
    between this page and sibling lab pages."""
    _login(client, monkeypatch)
    r = client.get("/lab/portfolio-vs-spy")
    assert r.status_code == 200
    assert "max-w-[2400px]" in r.text
    assert "max-w-5xl" not in r.text
