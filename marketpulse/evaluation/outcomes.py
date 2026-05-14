"""Outcome computation: scan pending events, compute forward returns, insert."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from marketpulse.data.service import DataService
from marketpulse.db.models import EvaluationEvent, EvaluationOutcome
from marketpulse.evaluation.benchmark import (
    BENCHMARK_TICKER,
    benchmark_forward_return,
)
from marketpulse.evaluation.forward_return import forward_return_at_horizon
from marketpulse.logging import get_logger

log = get_logger(__name__)

DEFAULT_HORIZONS = [1, 5, 20, 60]


@dataclass
class ComputeOutcomeReport:
    events_examined: int = 0
    outcomes_inserted: int = 0
    skipped_horizon_in_future: int = 0
    skipped_data_unavailable: int = 0
    skipped_benchmark_unavailable: int = 0
    skipped_already_computed: int = 0
    failed: int = 0
    failure_log: list[dict] = field(default_factory=list)


def compute_outcomes_for_pending_events(
    db: Session,
    data: DataService,
    horizons: list[int] | None = None,
    max_events: int = 500,
) -> ComputeOutcomeReport:
    """For each event without a matching outcome row at any of the requested
    horizons, compute the outcome and insert.

    Idempotent: safe to run multiple times per day. UNIQUE(event_id,
    horizon_trading_days) prevents duplicate inserts.

    Returns a report with per-status counts and a failure_log of dicts
    {event_id, ticker, horizon, reason}.
    """
    if horizons is None:
        horizons = DEFAULT_HORIZONS

    report = ComputeOutcomeReport()

    # Find events that might need outcomes computed.
    # Strategy: pull recent events; for each (event, horizon) check if outcome
    # row exists; if not, try to compute.
    events = (
        db.query(EvaluationEvent)
        .order_by(EvaluationEvent.event_time.desc())
        .limit(max_events)
        .all()
    )

    for event in events:
        report.events_examined += 1
        event_date = event.event_time.astimezone(UTC).date()

        for horizon in horizons:
            # Skip if outcome row already exists
            existing = (
                db.query(EvaluationOutcome.id)
                .filter(
                    EvaluationOutcome.event_id == event.id,
                    EvaluationOutcome.horizon_trading_days == horizon,
                )
                .first()
            )
            if existing:
                report.skipped_already_computed += 1
                continue

            # Compute forward return for the event
            event_result = forward_return_at_horizon(
                event.ticker, event_date, horizon, data,
            )
            if event_result is None:
                # Distinguish "horizon in future" from "data unavailable"
                # by checking event_date + horizon vs today.
                # Heuristic: if event_date is recent, it's horizon-in-future;
                # if event_date is old, it's data-unavailable.
                days_since_event = (datetime.now(UTC).date() - event_date).days
                if days_since_event < horizon * 1.5:
                    report.skipped_horizon_in_future += 1
                else:
                    report.skipped_data_unavailable += 1
                    report.failure_log.append({
                        "event_id": event.id,
                        "ticker": event.ticker,
                        "horizon": horizon,
                        "reason": "event_data_unavailable",
                    })
                continue

            # Compute benchmark forward return
            bench_return = benchmark_forward_return(event_date, horizon, data)
            if bench_return is None:
                report.skipped_benchmark_unavailable += 1
                report.failure_log.append({
                    "event_id": event.id,
                    "ticker": event.ticker,
                    "horizon": horizon,
                    "reason": "benchmark_unavailable",
                })
                continue

            # Insert outcome row
            try:
                outcome = EvaluationOutcome(
                    event_id=event.id,
                    horizon_trading_days=horizon,
                    event_price=event_result.event_price,
                    horizon_price=event_result.horizon_price,
                    horizon_date=event_result.horizon_date,
                    forward_return=event_result.forward_return,
                    benchmark_ticker=BENCHMARK_TICKER,
                    benchmark_forward_return=bench_return,
                    excess_return=event_result.forward_return - bench_return,
                )
                db.add(outcome)
                db.flush()
                report.outcomes_inserted += 1
            except Exception as exc:  # noqa: BLE001
                # IntegrityError from race condition or unexpected — log + continue
                db.rollback()
                report.failed += 1
                report.failure_log.append({
                    "event_id": event.id,
                    "ticker": event.ticker,
                    "horizon": horizon,
                    "reason": f"insert_failed: {exc}",
                })

    db.commit()
    return report
