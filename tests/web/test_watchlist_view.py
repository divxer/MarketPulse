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
