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


from unittest.mock import MagicMock

from marketpulse.ai.eval_analysis import run_eval_analysis
from marketpulse.ai.types import AnalysisResult


class FakeAi:
    """analyze() returns AnalysisResult; `behaviors` maps ticker -> 'fresh' |
    'cached' | 'raise'. Records the call order."""
    def __init__(self, behaviors):
        self.behaviors = behaviors
        self.calls = []

    def analyze(self, ticker):
        self.calls.append(ticker)
        b = self.behaviors.get(ticker, "fresh")
        if b == "raise":
            raise RuntimeError(f"boom {ticker}")
        return AnalysisResult(
            ticker=ticker, model="m", prompt_version="v",
            response_markdown="x",
            requested_at=datetime(2026, 5, 29, tzinfo=UTC),
            cached=(b == "cached"),
        )


RUN_DATE = date(2026, 5, 29)


def test_all_fresh_under_cap():
    ai = FakeAi({})
    s = run_eval_analysis(MagicMock(), ai=ai, universe=["A", "B", "C"],
                          max_calls=60, run_date=RUN_DATE)
    assert (s.analyzed_fresh, s.cache_hits, s.skipped_cap, s.cap_hit) == (3, 0, 0, False)
    assert s.processed + s.skipped_cap == s.universe_size


def test_same_day_rerun_all_cached_no_cap():
    ai = FakeAi({"A": "cached", "B": "cached"})
    s = run_eval_analysis(MagicMock(), ai=ai, universe=["A", "B"],
                          max_calls=1, run_date=RUN_DATE)
    # cache hits do NOT consume cap, so a cap of 1 still processes both
    assert (s.analyzed_fresh, s.cache_hits, s.skipped_cap, s.cap_hit) == (0, 2, 0, False)


def test_cap_hit_midloop_counts_fresh_only():
    ai = FakeAi({})
    s = run_eval_analysis(MagicMock(), ai=ai, universe=["A", "B", "C", "D"],
                          max_calls=2, run_date=RUN_DATE)
    assert (s.analyzed_fresh, s.skipped_cap, s.cap_hit) == (2, 2, True)
    assert ai.calls == ["A", "B"]                 # stopped after 2 fresh


def test_cache_hits_do_not_consume_cap():
    # A cached, B fresh, C cached, D fresh, E fresh; cap=2 fresh
    ai = FakeAi({"A": "cached", "C": "cached"})
    s = run_eval_analysis(MagicMock(), ai=ai, universe=["A", "B", "C", "D", "E"],
                          max_calls=2, run_date=RUN_DATE)
    assert s.analyzed_fresh == 2 and s.cache_hits == 2
    assert s.cap_hit is True and s.skipped_cap == 1    # E skipped
    assert s.processed + s.skipped_cap == 5


def test_max_calls_zero_nonempty_universe():
    ai = FakeAi({})
    s = run_eval_analysis(MagicMock(), ai=ai, universe=["A", "B"],
                          max_calls=0, run_date=RUN_DATE)
    assert (s.analyzed_fresh, s.skipped_cap, s.cap_hit) == (0, 2, True)
    assert ai.calls == []


def test_max_calls_zero_empty_universe_no_cap_hit():
    ai = FakeAi({})
    s = run_eval_analysis(MagicMock(), ai=ai, universe=[],
                          max_calls=0, run_date=RUN_DATE)
    assert (s.analyzed_fresh, s.skipped_cap, s.cap_hit) == (0, 0, False)


def test_per_ticker_error_rolls_back_and_continues():
    session = MagicMock()
    ai = FakeAi({"B": "raise"})
    s = run_eval_analysis(session, ai=ai, universe=["A", "B", "C"],
                          max_calls=60, run_date=RUN_DATE)
    assert (s.analyzed_fresh, s.errors, s.cap_hit) == (2, 1, False)
    assert ai.calls == ["A", "B", "C"]            # error didn't abort
    session.rollback.assert_called_once()          # cleaned partial state
    assert s.processed + s.skipped_cap == 3


def test_mixed_path_invariant():
    # fresh=2 (A,D), cache_hit=1 (B), error=1 (C) over 7, cap=2 → E,F,G skipped
    ai = FakeAi({"B": "cached", "C": "raise"})
    s = run_eval_analysis(MagicMock(), ai=ai,
                          universe=["A", "B", "C", "D", "E", "F", "G"],
                          max_calls=2, run_date=RUN_DATE)
    assert s.analyzed_fresh == 2 and s.cache_hits == 1 and s.errors == 1
    assert s.processed == 4 and s.skipped_cap == 3
    assert s.processed + s.skipped_cap == 7
