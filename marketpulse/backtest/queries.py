"""DB queries for backtest simulator — joins EvaluationEvent + Outcome."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


@dataclass(frozen=True)
class EventOutcomePair:
    """Flattened (event, outcome) row used by the simulator.

    Includes only the fields the simulator actually needs — avoids
    holding ORM-attached objects across simulator iterations.
    """
    ticker: str
    event_time: datetime
    event_price: float
    horizon_price: float
    horizon_date: date
    forward_return: float
    benchmark_forward_return: float


def get_bullish_events_with_outcomes(
    db: Session,
    *,
    strategy: str,
    horizon: int,
    since: date | None = None,
) -> list[EventOutcomePair]:
    """Bullish events for one strategy at one horizon, with mature outcomes.

    Filters (spec § Architecture + § Open Decisions #14, #16):
      - event.event_type == "ai_analysis"
      - event.subtype == "bullish"
      - event.payload["source"] == "stock_analysis"  (Decision #14)
      - event.payload["strategy"] == strategy
      - outcome.horizon_trading_days == horizon
      - (since is None) OR (event.event_time >= since)
      - event.event_time.date() < outcome.horizon_date  (Decision #16)

    Returns:
        Sorted ASC by event_time (entry order for the simulator).
    """
    stmt = (
        select(
            EvaluationEvent.ticker,
            EvaluationEvent.event_time,
            EvaluationOutcome.event_price,
            EvaluationOutcome.horizon_price,
            EvaluationOutcome.horizon_date,
            EvaluationOutcome.forward_return,
            EvaluationOutcome.benchmark_forward_return,
        )
        .join(EvaluationOutcome, EvaluationOutcome.event_id == EvaluationEvent.id)
        .where(EvaluationEvent.event_type == "ai_analysis")
        .where(EvaluationEvent.subtype == "bullish")
        .where(EvaluationOutcome.horizon_trading_days == horizon)
        .where(func.json_extract(EvaluationEvent.payload, "$.source") == "stock_analysis")
        .where(func.json_extract(EvaluationEvent.payload, "$.strategy") == strategy)
        .where(func.date(EvaluationEvent.event_time) < EvaluationOutcome.horizon_date)
        .order_by(EvaluationEvent.event_time.asc())
    )
    if since is not None:
        stmt = stmt.where(
            EvaluationEvent.event_time >= datetime.combine(
                since, datetime.min.time(), tzinfo=UTC,
            ),
        )

    rows = db.execute(stmt).all()
    return [
        EventOutcomePair(
            ticker=r.ticker,
            event_time=r.event_time,
            event_price=r.event_price,
            horizon_price=r.horizon_price,
            horizon_date=r.horizon_date,
            forward_return=r.forward_return,
            benchmark_forward_return=r.benchmark_forward_return,
        )
        for r in rows
    ]
