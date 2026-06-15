# Layer: cli
"""Phase 8c-1 shadow batch: python -m marketpulse.cli.run_swarm_research
   --tickers AAPL,NVDA --as-of 2026-06-15
Records swarm_research verdicts as ai_analysis events. Default OFF: requires
SWARM_RESEARCH_ENABLED=true AND SWARM_RESEARCH_API_KEY. Never auto-runs in the
daily tick. NOT an allocator/execution path."""
from __future__ import annotations

import argparse
import contextlib
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from marketpulse.config import get_settings
from marketpulse.db.base import session_scope
from marketpulse.evaluation.events import record_event
from marketpulse.logging import get_logger

log = get_logger(__name__)


@dataclass
class BatchResult:
    recorded: int = 0
    abstained: int = 0
    failed: int = 0


def run_batch(db, *, tickers, as_of, provider, price_provider) -> BatchResult:
    """Three-state semantics (review fix #3):
      abstained = provider returned no verdict (None, or raised — isolated here).
      failed    = HAD a verdict but couldn't record it (no event_price, or
                  record_event rejected it).
      recorded  = event written.
    """
    # Review #4: event_time is the as-of END-OF-DAY in UTC (not 00:00), matching
    # the spec's "as_of EOD UTC" and the event_price (as_of close).
    event_time = datetime.combine(as_of, time(23, 59, 59), tzinfo=UTC)
    res = BatchResult()
    for ticker in tickers:
        # Provider isolation: a stub/future provider might raise.
        try:
            v = provider.verdict_for(ticker=ticker, as_of=as_of)
        except Exception as exc:  # noqa: BLE001
            res.abstained += 1
            log.warning("swarm_research_provider_error", ticker=ticker, error=str(exc))
            continue
        if v is None:
            res.abstained += 1
            log.info("swarm_research_abstained", ticker=ticker)
            continue
        # Review (Important): price lookup AND record_event can raise too —
        # isolate them per ticker and COMMIT per recorded ticker so a later
        # failure never rolls back earlier-recorded verdicts.
        try:
            close = price_provider.close_on_date(ticker=ticker, on_date=as_of)
            if close is None:
                res.failed += 1   # had a verdict, lost it to a missing price
                log.warning("swarm_research_no_price", ticker=ticker)
                continue
            record_event(
                event_type="ai_analysis", subtype=v.verdict, ticker=ticker,
                event_time=event_time,
                event_price=float(close.price),
                # Critical fix: research_only=True keeps this arm out of the
                # paper-trading allocator. BidAggregator.collect_for_date skips
                # research_only events; permutation.load_rows ignores the flag,
                # so the arm is still measured. Without this a same-day swarm
                # event becomes an executable BidCandidate (broken isolation).
                payload={"source": "swarm", "strategy": "swarm_research",
                         "research_only": True, "provenance": v.provenance},
                db=db,
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001 — per-ticker isolation
            db.rollback()
            res.failed += 1
            log.warning("swarm_research_record_failed", ticker=ticker, error=str(exc))
            continue
        res.recorded += 1
        log.info("swarm_research_recorded", ticker=ticker, verdict=v.verdict)
    return res


def _build_provider(settings, goal: str):
    from marketpulse.research.swarm_provider import HttpVibeSwarmProvider
    return HttpVibeSwarmProvider(
        base_url=settings.swarm_research_base_url,
        api_key=settings.swarm_research_api_key,
        preset=settings.swarm_research_preset,
        timeout_seconds=settings.swarm_research_timeout_seconds,
        goal=goal,
    )


def main(argv: list[str] | None = None, *, provider=None, price_provider=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", required=True, help="comma-separated")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--goal", default="Assess 5-trading-day outlook vs SPY.")
    args = ap.parse_args(argv)

    settings = get_settings()
    if not settings.swarm_research_enabled or not settings.swarm_research_api_key:
        print("swarm research disabled: set SWARM_RESEARCH_ENABLED=true and "
              "SWARM_RESEARCH_API_KEY", file=sys.stderr)
        raise SystemExit(1)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    cap = settings.swarm_research_max_tickers_per_run
    if len(tickers) > cap:
        print(f"too many tickers ({len(tickers)} > cap {cap})", file=sys.stderr)
        raise SystemExit(1)
    as_of = date.fromisoformat(args.as_of) if args.as_of \
        else datetime.now(UTC).date()

    if provider is None:
        provider = _build_provider(settings, args.goal)
    if price_provider is None:
        # Review #5: reuse the SAME close_on_date provider the paper engine uses
        # (marketpulse/trading/price_provider.YFinancePriceProvider) so event_price
        # is drawn from the identical price path as other evaluation events — keeps
        # the swarm arm's prices comparable, no separate ad-hoc quote source. It
        # resolves last-final-close ≤ as_of (post-P2F), the right reference price.
        from marketpulse.data.yfinance_client import YFinanceClient
        from marketpulse.trading.price_provider import YFinancePriceProvider
        price_provider = YFinancePriceProvider(client=YFinanceClient())

    # NOTE (review #4): session_scope is a PLAIN GENERATOR in this repo
    # (marketpulse/db/base.py — bare yield + finally, NOT @contextmanager).
    # Manual `next(gen)` driving is the verified convention (finalize_prices,
    # refresh_sectors, rebuild_nav_snapshots all do this). Do NOT rewrite to
    # `with session_scope() as db:` — it would raise.
    gen = session_scope()
    db = next(gen)
    try:
        # run_batch commits per recorded ticker (per-ticker isolation); no
        # batch-level commit needed.
        res = run_batch(db, tickers=tickers, as_of=as_of,
                        provider=provider, price_provider=price_provider)
        print(f"swarm_research {as_of}: recorded={res.recorded} "
              f"abstained={res.abstained} failed={res.failed}")
        # Review (Important): total failure must NOT exit 0 — recorded=0 means
        # the run produced no samples (all abstained/failed); surface it.
        if res.recorded == 0:
            print("swarm_research: no verdicts recorded (all abstained/failed)",
                  file=sys.stderr)
            raise SystemExit(1)
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


if __name__ == "__main__":
    main()
