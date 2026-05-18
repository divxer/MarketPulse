import json
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.base import session_scope
from marketpulse.db.models import DailyRecap


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed_recap(d: date, status: str = "success") -> None:
    gen = session_scope()
    db = next(gen)
    try:
        db.add(DailyRecap(
            recap_date=d, generation_status=status,
            ai_commentary_text="ok",
            generated_at=datetime.now(UTC),
        ))
        db.commit()
    finally:
        db.close()


def test_recap_detail(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    _seed_recap(date(2026, 5, 8))
    res = client.get("/recap/2026-05-08")
    assert res.status_code == 200
    assert "2026-05-08" in res.text


def test_recap_list(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    _seed_recap(date(2026, 5, 7))
    _seed_recap(date(2026, 5, 8))
    res = client.get("/recaps")
    assert res.status_code == 200
    assert "2026-05-07" in res.text and "2026-05-08" in res.text


# ---------------------------------------------------------------------------
# Phase 5e Task 4 — new context-enrichment tests
# ---------------------------------------------------------------------------

def _seed_recap_full(db_session, recap_date, *, status="success",
                     market_summary=None, holdings_totals=None,
                     watchlist=None, key_events=None,
                     commentary="测试复盘正文。"):
    r = DailyRecap(
        recap_date=recap_date,
        generation_status=status,
        ai_commentary_text=commentary,
        market_summary_json=json.dumps(market_summary) if market_summary else None,
        watchlist_performance_json=json.dumps(watchlist) if watchlist else None,
        holdings_totals_json=json.dumps(holdings_totals) if holdings_totals else None,
        key_events_json=json.dumps(key_events) if key_events else None,
        generated_at=datetime(2026, 5, 12, 20, 42, tzinfo=UTC) if status == "success" else None,
    )
    db_session.add(r)
    db_session.commit()
    return r


def test_recap_detail_normalizes_market_snap_dict_to_list(client, monkeypatch, db_session):
    """Stored flat dict {spy, qqq, dia, vix} must reshape to list of cards."""
    _login(client, monkeypatch)
    _seed_recap_full(db_session, date(2026, 5, 12), market_summary={
        "spy": 0.24, "qqq": 0.44, "dia": 0.51, "vix": 14.18,
    })
    r = client.get("/recap/2026-05-12")
    assert r.status_code == 200
    assert "标普 500" in r.text
    assert "纳指 100" in r.text
    assert "道指" in r.text
    assert "VIX" in r.text


def test_recap_detail_handles_missing_jsons_gracefully(client, monkeypatch, db_session):
    """All *_json fields NULL → page renders with placeholders."""
    _login(client, monkeypatch)
    _seed_recap_full(db_session, date(2026, 5, 12),
                     market_summary=None, holdings_totals=None,
                     watchlist=None, key_events=None)
    r = client.get("/recap/2026-05-12")
    assert r.status_code == 200


def test_recap_detail_404_when_no_row(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/recap/2020-01-01")
    assert r.status_code == 404


def test_recap_detail_prev_recaps_filters_strictly_past(client, monkeypatch, db_session):
    """prev_recaps filter is < recap_date, not just !=."""
    _login(client, monkeypatch)
    _seed_recap_full(db_session, date(2026, 5, 10), commentary="过去 1")
    _seed_recap_full(db_session, date(2026, 5, 11), commentary="过去 2")
    _seed_recap_full(db_session, date(2026, 5, 12), commentary="当日")
    _seed_recap_full(db_session, date(2026, 5, 13), commentary="未来不该出现")
    r = client.get("/recap/2026-05-12")
    assert r.status_code == 200
    assert "未来不该出现" not in r.text


def test_recaps_list_extracts_pl_from_holdings_totals(client, monkeypatch, db_session):
    """compute_totals key is 'pl_dollars' (NOT 'today_pl_dollars')."""
    _login(client, monkeypatch)
    _seed_recap_full(db_session, date(2026, 5, 12), holdings_totals={
        "cost": 10000.0, "market_value": 10500.0,
        "pl_dollars": 500.0, "pl_pct": 5.0,
    })
    r = client.get("/recaps")
    assert r.status_code == 200
    # The +500 dollars should appear (formatted)
    assert "+500" in r.text or "500.00" in r.text


# ---------------------------------------------------------------------------
# Phase 5e Task 5 — recap.html shell + layout CSS
# ---------------------------------------------------------------------------

def test_recap_page_visual_anchors_present(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap_full(db_session, date(2026, 5, 12))
    r = client.get("/recap/2026-05-12")
    for cls in ("mp-recap-hero", "mp-recap-snap", "mp-recap-body",
                "mp-recap-article", "mp-recap-rail"):
        assert cls in r.text, f"missing {cls}"


def test_recap_page_uses_2400_max_width(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap_full(db_session, date(2026, 5, 12))
    r = client.get("/recap/2026-05-12")
    assert "max-w-[2400px]" in r.text


def test_recap_page_has_recap_toast_function(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap_full(db_session, date(2026, 5, 12))
    r = client.get("/recap/2026-05-12")
    assert "function recapToast" in r.text
    assert "localizeTimes" in r.text
