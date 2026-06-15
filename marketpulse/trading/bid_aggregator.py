"""Phase 6a BidAggregator — read-only NY-day window over evaluation_event.

Intentionally dumb. No DEDUP / sizing / capping — that's allocate_for_day's
job. No horizon_price lookup — that's daily_cycle's job via PriceProvider.

EvaluationEvent stores per-strategy metadata inside its ``payload`` JSON
column (see marketpulse/backtest/queries.py). Rows whose payload has no
``strategy`` key are SKIPPED (no audit in 6a; 6b may add
BID_SKIPPED_NO_STRATEGY if needed)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketpulse.backtest.allocation import BidCandidate
from marketpulse.db.models import EvaluationEvent
from marketpulse.trading.calendar import NYTradingCalendar

_NY = ZoneInfo("America/New_York")


class BidAggregator:
    def __init__(self, *, session: Session, calendar: NYTradingCalendar) -> None:
        self._session = session
        self._calendar = calendar

    def collect_for_date(self, tick_date: date) -> list[BidCandidate]:
        """Read evaluation_event rows whose event_time falls in
        [NY-midnight(tick_date), NY-midnight(tick_date+1)) converted
        to UTC. Forward-only (lock xxxiii): only today's events."""
        ny_start = datetime.combine(tick_date, datetime.min.time(), tzinfo=_NY)
        ny_end = ny_start + timedelta(days=1)
        utc_start = ny_start.astimezone(UTC)
        utc_end = ny_end.astimezone(UTC)

        rows = self._session.execute(
            select(EvaluationEvent)
            .where(EvaluationEvent.event_time >= utc_start)
            .where(EvaluationEvent.event_time < utc_end)
            .order_by(EvaluationEvent.event_time)
        ).scalars().all()

        bids: list[BidCandidate] = []
        for r in rows:
            payload = r.payload or {}
            # Research-only events (e.g. the swarm_research shadow arm) carry a
            # strategy label so the permutation pipeline measures them, but they
            # MUST NOT become executable bids. Skip them here — the allocator-side
            # half of the research/execution isolation invariant.
            if payload.get("research_only"):
                continue
            strategy = payload.get("strategy")
            if not strategy:
                continue  # skip NULL-strategy rows (no audit in 6a)
            strategy_version = payload.get("strategy_version") or "v0"

            # horizon_date computed deterministically; horizon_price stays
            # None here. daily_cycle fills it via PriceProvider before
            # constructing the OrderRequest.
            event_date = r.event_time.astimezone(_NY).date()
            horizon_date = event_date
            for _ in range(5):
                horizon_date = self._calendar.next_business_day(horizon_date)

            bids.append(BidCandidate(
                strategy=strategy,
                ticker=r.ticker,
                event_time=r.event_time,
                event_price=float(r.event_price or 0.0),
                horizon_date=horizon_date,
                horizon_price=None,  # filled by daily_cycle via PriceProvider
                strategy_version=strategy_version,
            ))
        return bids
