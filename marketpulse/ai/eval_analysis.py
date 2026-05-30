# Layer: orchestration
"""Task #57 — nightly eval-analysis core (testable service/orchestration layer).

Eval-ONLY: the only side-effecting call here is `AiService.analyze()`, which
writes AiAnalysis + EvaluationEvent and commits internally. This module must
never import the allocator / order-placement / watchlist-mutation layers — a CI
architecture guard (tests/architecture/test_eval_only_invariant.py) enforces it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from marketpulse.db.models import WatchlistItem
from marketpulse.logging import get_logger
from marketpulse.trading.repository import Repository as PaperPositionRepository

log = get_logger(__name__)


@dataclass(frozen=True)
class EvalAnalysisSummary:
    run_date: date
    universe_size: int
    analyzed_fresh: int
    cache_hits: int
    skipped_cap: int
    errors: int
    cap_hit: bool

    @property
    def processed(self) -> int:
        return self.analyzed_fresh + self.cache_hits + self.errors

    def as_dict(self, *, status: str, error: str | None = None) -> dict[str, object]:
        """status ∈ {"ok", "disabled", "failed"}. `ts` is added by the
        persistence layer (record_eval_run_summary), keeping this clock-free."""
        d = {
            "status": status,
            "run_date": self.run_date,
            "universe_size": self.universe_size,
            "analyzed_fresh": self.analyzed_fresh,
            "cache_hits": self.cache_hits,
            "skipped_cap": self.skipped_cap,
            "errors": self.errors,
            "cap_hit": self.cap_hit,
            "processed": self.processed,
        }
        if error is not None:
            d["error"] = error
        return d


def build_eval_universe(session) -> list[str]:
    """Watchlist ∪ current open paper holdings, normalized + deduped + sorted ASC.

    Reads only: WatchlistItem (model) and the canonical open-positions helper
    (`Repository.open_positions_snapshot`, status == "OPEN"). No mutation.
    Sorted ASC so the cap-skip set is deterministic.
    """
    watch_rows = session.query(WatchlistItem.ticker).all()
    holdings = PaperPositionRepository(session=session).open_positions_snapshot()
    raw = [r[0] for r in watch_rows] + [p.ticker for p in holdings]
    normalized = {t.strip().upper() for t in raw if t and t.strip()}
    return sorted(normalized)


def run_eval_analysis(
    session, *, ai, universe: list[str], max_calls: int, run_date: date,
) -> EvalAnalysisSummary:
    """Loop the prebuilt universe calling ai.analyze() under a FRESH-call cap.

    Cap semantics: cache hits and errors do NOT consume the budget; only fresh
    (cached=False) analyses do. Reaching the cap stops the loop, sets cap_hit and
    skipped_cap. max_calls <= 0 stops before the first call (the `fresh >=
    max_calls` guard fires at 0 >= 0) — but only when the universe is non-empty,
    so an empty universe never sets cap_hit.

    Per-ticker errors are isolated: analyze() commits internally, so a raise can
    leave partial uncommitted state — rollback before the next ticker. Never
    raises; always returns a summary.
    """
    fresh = cache_hits = errors = 0
    cap_hit = False
    skipped = 0
    for processed, ticker in enumerate(universe):  # processed = count done so far
        if fresh >= max_calls:                    # cap counts FRESH calls only
            cap_hit = True
            skipped = len(universe) - processed
            log.warning(
                "ai_eval_daily_cap_hit", fresh=fresh,
                universe_size=len(universe), skipped=skipped,
                run_date=str(run_date),
            )
            break
        try:
            result = ai.analyze(ticker)           # commits AiAnalysis + EvaluationEvent
            if result.cached:
                cache_hits += 1
            else:
                fresh += 1
        except Exception as exc:                  # per-ticker isolation
            session.rollback()                    # clean partial state before next
            errors += 1
            log.warning("ai_eval_ticker_failed", ticker=ticker, error=str(exc))
    return EvalAnalysisSummary(
        run_date=run_date, universe_size=len(universe),
        analyzed_fresh=fresh, cache_hits=cache_hits, skipped_cap=skipped,
        errors=errors, cap_hit=cap_hit,
    )
