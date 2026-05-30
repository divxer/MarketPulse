# Layer: test
"""Watchlist AI-Universe presenter."""
from __future__ import annotations

from marketpulse.web.watchlist_view import (
    WatchlistCard,
    _fmt_price,
    _fmt_change,
    _verdict_fields,
    _status_fields,
)


def test_fmt_price():
    assert _fmt_price(450.236) == "$450.24"
    assert _fmt_price(None) == "—"


def test_fmt_change():
    assert _fmt_change(450.0, 446.0) == ("+0.90%", "mp-watchlist__chg--up")
    assert _fmt_change(440.0, 446.0) == ("-1.35%", "mp-watchlist__chg--down")
    assert _fmt_change(450.0, None) == ("—", "")
    assert _fmt_change(None, 446.0) == ("—", "")
    assert _fmt_change(446.0, 0.0) == ("—", "")  # no div-by-zero


def test_verdict_fields():
    assert _verdict_fields("bullish") == ("mp-ai-badge--good", "Bullish")
    assert _verdict_fields("bearish") == ("mp-ai-badge--bad", "Bearish")
    assert _verdict_fields("neutral") == ("mp-ai-badge--neutral", "Neutral")
    assert _verdict_fields(None) == ("mp-ai-badge--pending", "Pending")


def test_status_fields():
    assert _status_fields("AAPL", {"AAPL"}, set()) == ("Holding", "mp-chip--success")
    assert _status_fields("QBTS", set(), {"QBTS"}) == ("Paper Position", "mp-chip--warn")
    assert _status_fields("SPY", set(), set()) == ("Universe Only", "mp-chip--muted")
    # holdings wins over paper if somehow both
    assert _status_fields("X", {"X"}, {"X"}) == ("Holding", "mp-chip--success")


def test_watchlistcard_spark_stroke_default():
    c = WatchlistCard("AAPL", "—", "—", "", [], "X", "mp-ai-badge--pending",
                      "Pending", "Universe Only", "mp-chip--muted")
    assert c.spark_stroke == "var(--mp-up)"  # default; presenter overrides on down
    assert c.item_id is None and c.active is False


from datetime import UTC, date, datetime  # noqa: E402
from decimal import Decimal  # noqa: E402

from marketpulse.db.models import (  # noqa: E402
    EvaluationEvent, Holding, PaperPosition, PriceCacheEntry, WatchlistItem,
)
from marketpulse.web.watchlist_view import (  # noqa: E402
    _price_blocks, _latest_verdicts, _status_sets,
)


def _add_price(s, ticker, d, close):
    s.add(PriceCacheEntry(ticker=ticker, date=d, open=close, high=close,
                          low=close, close=close, volume=1))


def test_price_blocks_latest_and_prior(db_session):
    _add_price(db_session, "AAPL", date(2026, 5, 28), 446.0)
    _add_price(db_session, "AAPL", date(2026, 5, 29), 450.0)
    _add_price(db_session, "SOLO", date(2026, 5, 29), 12.0)  # single row
    db_session.commit()
    blocks = _price_blocks(db_session, ["AAPL", "SOLO", "ZZZZ"])
    assert blocks["AAPL"]["latest"] == 450.0
    assert blocks["AAPL"]["prior"] == 446.0
    assert blocks["AAPL"]["spark"][-2:] == [446.0, 450.0]
    assert blocks["SOLO"]["prior"] is None
    assert blocks["SOLO"]["spark"] == []  # <2 points → empty (contract)
    assert "ZZZZ" not in blocks  # no rows → absent


def test_latest_verdicts_newest_wins(db_session):
    for st, t in [("neutral", datetime(2026, 5, 20, tzinfo=UTC)),
                  ("bullish", datetime(2026, 5, 29, tzinfo=UTC))]:
        db_session.add(EvaluationEvent(
            event_type="ai_analysis", subtype=st, ticker="AAPL",
            event_time=t, event_price=1.0, payload={}))
    db_session.commit()
    assert _latest_verdicts(db_session, ["AAPL"])["AAPL"] == "bullish"


def test_status_sets(db_session):
    db_session.add(Holding(ticker="AAPL", quantity=1, avg_cost=1))
    db_session.add(PaperPosition(
        order_id=9001, strategy="momentum_breakout", ticker="QBTS",
        quantity=1, entry_price=Decimal("1"), status="OPEN",
        opened_at=datetime(2026, 5, 1, tzinfo=UTC), entry_date=date(2026, 5, 1),
        horizon_date=date(2026, 6, 1)))
    db_session.commit()
    holdings, paper = _status_sets(db_session)
    assert "AAPL" in holdings and "QBTS" in paper


def test_sector_map_priority(monkeypatch):
    import marketpulse.web.watchlist_view as wv
    monkeypatch.setattr(wv, "load_sector_cache",
                        lambda: {"A": "CacheSec", "B": "CacheSec", "C": "CacheSec"})
    monkeypatch.setattr(wv, "load_sector_overrides", lambda: {"B": "OverrideSec"})
    out = wv._sector_map(["A", "B", "C", "D"], {"A": "HoldSec"})
    assert out["A"] == "HoldSec"        # holdings.sector wins over cache
    assert out["B"] == "OverrideSec"    # override beats cache
    assert out["C"] == "CacheSec"       # pure cache hit
    assert out["D"] == wv.UNCATEGORIZED  # uncached → Uncategorized


def _seed_universe(db_session):
    # 3 tickers: AAPL (holding, tech), MSFT (universe, tech), SPY (universe, ETF)
    for t in ["AAPL", "MSFT", "SPY"]:
        db_session.add(WatchlistItem(ticker=t))
    db_session.add(Holding(ticker="AAPL", quantity=1, avg_cost=1, sector="Technology"))
    _add_price(db_session, "AAPL", date(2026, 5, 28), 100.0)
    _add_price(db_session, "AAPL", date(2026, 5, 29), 101.0)
    db_session.add(EvaluationEvent(event_type="ai_analysis", subtype="bullish",
                   ticker="MSFT", event_time=datetime(2026, 5, 29, tzinfo=UTC),
                   event_price=1.0, payload={}))
    db_session.commit()


def test_build_view_groups_order_and_coverage(db_session, monkeypatch):
    import marketpulse.web.watchlist_view as wv
    # Force deterministic sectors (cache-only): MSFT->Technology, SPY->ETF
    monkeypatch.setattr(wv, "load_sector_cache", lambda: {"MSFT": "Technology", "SPY": "ETF"})
    monkeypatch.setattr(wv, "load_sector_overrides", lambda: {})
    _seed_universe(db_session)

    view = wv.build_watchlist_view(db_session)
    names = [g.name for g in view.groups]
    # Technology(2) before ETF(1); Uncategorized would be last if present
    assert names == ["Technology", "ETF"]
    tech = view.groups[0]
    assert tech.count == 2
    assert [c.ticker for c in tech.cards] == ["AAPL", "MSFT"]  # ticker ASC
    assert tech.cards[0].status_label == "Holding"
    assert tech.cards[0].price_display == "$101.00"
    assert tech.cards[1].verdict_label == "Bullish"            # MSFT
    assert tech.cards[1].status_label == "Universe Only"
    assert view.coverage.total == 3
    assert view.coverage.sectors == 2
    assert view.coverage.holdings == 1
    assert view.coverage.universe_only == 2


def test_build_view_uncategorized_last(db_session, monkeypatch):
    import marketpulse.web.watchlist_view as wv
    monkeypatch.setattr(wv, "load_sector_cache", lambda: {"MSFT": "Technology"})
    monkeypatch.setattr(wv, "load_sector_overrides", lambda: {})
    db_session.add(WatchlistItem(ticker="MSFT"))
    db_session.add(WatchlistItem(ticker="ZZZZ"))  # uncached → Uncategorized
    db_session.commit()
    view = wv.build_watchlist_view(db_session)
    assert [g.name for g in view.groups][-1] == "Uncategorized"
