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
