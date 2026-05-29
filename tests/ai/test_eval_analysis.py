# Layer: test
"""Task #57 — eval-analysis core: summary dataclass, universe, run loop."""
from __future__ import annotations

from datetime import date

from marketpulse.ai.eval_analysis import EvalAnalysisSummary


def test_summary_processed_and_invariant():
    s = EvalAnalysisSummary(
        run_date=date(2026, 5, 29), universe_size=7,
        analyzed_fresh=2, cache_hits=1, skipped_cap=3, errors=1, cap_hit=True,
    )
    assert s.processed == 4                      # fresh + cache_hits + errors
    assert s.processed + s.skipped_cap == s.universe_size


def test_summary_as_dict_ok():
    s = EvalAnalysisSummary(
        run_date=date(2026, 5, 29), universe_size=3,
        analyzed_fresh=3, cache_hits=0, skipped_cap=0, errors=0, cap_hit=False,
    )
    d = s.as_dict(status="ok")
    assert d == {
        "status": "ok", "run_date": date(2026, 5, 29), "universe_size": 3,
        "analyzed_fresh": 3, "cache_hits": 0, "skipped_cap": 0, "errors": 0,
        "cap_hit": False, "processed": 3,
    }
    assert "error" not in d


def test_summary_as_dict_failed_includes_error():
    s = EvalAnalysisSummary(date(2026, 5, 29), 0, 0, 0, 0, 0, False)
    d = s.as_dict(status="failed", error="boom")
    assert d["status"] == "failed"
    assert d["error"] == "boom"


from datetime import UTC, datetime
from decimal import Decimal

from marketpulse.ai.eval_analysis import build_eval_universe
from marketpulse.db.models import PaperPosition, WatchlistItem

_order_seq = iter(range(1, 100_000))


def _add_watch(session, ticker, order=0):
    session.add(WatchlistItem(ticker=ticker, sort_order=order))


def _add_open_position(session, ticker):
    session.add(PaperPosition(
        order_id=next(_order_seq),
        strategy="momentum_breakout",
        ticker=ticker,
        quantity=1,
        entry_price=Decimal("10"),
        status="OPEN",
        opened_at=datetime(2026, 5, 1, tzinfo=UTC),
        entry_date=date(2026, 5, 1),
        horizon_date=date(2026, 6, 1),
    ))


def _add_closed_position(session, ticker):
    # CLOSED requires entry_fill_id + exit_fill_id NOT NULL (CHECK constraint).
    session.add(PaperPosition(
        order_id=next(_order_seq),
        strategy="momentum_breakout",
        ticker=ticker,
        quantity=1,
        entry_price=Decimal("10"),
        status="CLOSED",
        opened_at=datetime(2026, 5, 1, tzinfo=UTC),
        closed_at=datetime(2026, 5, 10, tzinfo=UTC),
        entry_fill_id=1,
        exit_fill_id=2,
        exit_price=Decimal("11"),
        entry_date=date(2026, 5, 1),
        horizon_date=date(2026, 6, 1),
    ))


def test_universe_union_dedup_sorted(db_session):
    _add_watch(db_session, "AAPL")
    _add_watch(db_session, "MSFT")
    _add_open_position(db_session, "AAPL")     # overlaps watchlist
    _add_open_position(db_session, "QUBT")     # holding not on watchlist
    db_session.commit()
    assert build_eval_universe(db_session) == ["AAPL", "MSFT", "QUBT"]


def test_universe_normalizes_case_and_whitespace(db_session):
    _add_watch(db_session, " aapl ")
    _add_open_position(db_session, "qubt")
    db_session.commit()
    assert build_eval_universe(db_session) == ["AAPL", "QUBT"]


def test_universe_excludes_closed_positions(db_session):
    _add_closed_position(db_session, "TSLA")
    db_session.commit()
    assert build_eval_universe(db_session) == []


def test_universe_empty(db_session):
    assert build_eval_universe(db_session) == []
