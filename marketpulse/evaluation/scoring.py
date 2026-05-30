"""Hit-rate queries over EvaluationEvent + EvaluationOutcome.

All functions are pure read; do not mutate state.

Threshold conventions:
- NEUTRAL_THRESHOLD = 0.01 (1% excess return).
- Directional verdicts (bullish/bearish) use STRICT inequality:
  bullish hit ⇔ excess_return > +0.01
  bearish hit ⇔ excess_return < -0.01
- Neutral verdicts use INCLUSIVE inequality:
  neutral hit ⇔ |excess_return| <= 0.01
At exactly ±threshold, neutral hits and directional miss.

Platform note: source filter uses SQLite json_extract — SQLite-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from marketpulse.db.models import EvaluationEvent, EvaluationOutcome
from marketpulse.evaluation.constants import AIVerdict

NEUTRAL_THRESHOLD = 0.01


@dataclass(frozen=True)
class HitRateStats:
    n_total: int
    n_hits: int
    n_bullish: int
    n_bearish: int
    n_neutral: int
    n_bullish_hits: int
    n_bearish_hits: int
    n_neutral_hits: int
    hit_rate: float | None
    avg_excess_return: float
    as_of: datetime


def _is_hit(subtype: str, excess: float) -> bool:
    """Apply scoring rules per spec §threshold."""
    if subtype == AIVerdict.BULLISH:
        return excess > NEUTRAL_THRESHOLD
    if subtype == AIVerdict.BEARISH:
        return excess < -NEUTRAL_THRESHOLD
    if subtype == AIVerdict.NEUTRAL:
        return abs(excess) <= NEUTRAL_THRESHOLD
    return False


def compute_hit_rate(
    db: Session,
    *,
    event_type: str = "ai_analysis",
    subtype: str | None = None,
    ticker: str | None = None,
    horizon: int = 5,
    source: str | None = None,
    strategy: str | None = None,
    since: date | None = None,
) -> HitRateStats:
    """Core hit-rate computation.

    Single query fetches all (event, outcome) pairs matching filters at
    the given horizon; aggregation happens in Python.
    """
    stmt = (
        select(
            EvaluationEvent.subtype,
            EvaluationOutcome.excess_return,
        )
        .join(EvaluationOutcome, EvaluationOutcome.event_id == EvaluationEvent.id)
        .where(EvaluationEvent.event_type == event_type)
        .where(EvaluationOutcome.horizon_trading_days == horizon)
    )
    if subtype is not None:
        stmt = stmt.where(EvaluationEvent.subtype == subtype)
    if ticker is not None:
        stmt = stmt.where(EvaluationEvent.ticker == ticker)
    if source is not None:
        # SQLite-only: json_extract on payload
        stmt = stmt.where(
            func.json_extract(EvaluationEvent.payload, "$.source") == source,
        )
    if strategy is not None:
        stmt = stmt.where(
            func.json_extract(EvaluationEvent.payload, "$.strategy") == strategy,
        )
    if since is not None:
        stmt = stmt.where(
            EvaluationEvent.event_time >= datetime.combine(since,
                                                            datetime.min.time(),
                                                            tzinfo=UTC),
        )

    rows = db.execute(stmt).all()

    n_bullish = n_bearish = n_neutral = 0
    n_bullish_hits = n_bearish_hits = n_neutral_hits = 0
    total_excess = 0.0
    for sub, excess in rows:
        if sub == AIVerdict.BULLISH:
            n_bullish += 1
            if _is_hit(sub, excess):
                n_bullish_hits += 1
        elif sub == AIVerdict.BEARISH:
            n_bearish += 1
            if _is_hit(sub, excess):
                n_bearish_hits += 1
        elif sub == AIVerdict.NEUTRAL:
            n_neutral += 1
            if _is_hit(sub, excess):
                n_neutral_hits += 1
        total_excess += excess

    n_total = n_bullish + n_bearish + n_neutral
    n_hits = n_bullish_hits + n_bearish_hits + n_neutral_hits
    hit_rate = (n_hits / n_total) if n_total > 0 else None
    avg_excess = (total_excess / n_total) if n_total > 0 else 0.0

    return HitRateStats(
        n_total=n_total,
        n_hits=n_hits,
        n_bullish=n_bullish,
        n_bearish=n_bearish,
        n_neutral=n_neutral,
        n_bullish_hits=n_bullish_hits,
        n_bearish_hits=n_bearish_hits,
        n_neutral_hits=n_neutral_hits,
        hit_rate=hit_rate,
        avg_excess_return=avg_excess,
        as_of=datetime.now(UTC),
    )


@dataclass(frozen=True)
class TickerHitRate:
    ticker: str
    n_total: int
    n_hits: int
    hit_rate: float | None
    avg_excess_return: float


def get_per_ticker_hit_rates(
    db: Session,
    *,
    horizon: int = 5,
    source: str | None = None,
    strategy: str | None = None,
    subtype: str | None = None,
    since: date | None = None,
) -> list[TickerHitRate]:
    """Per-ticker rollup, sorted by hit_rate desc.

    Tickers with n_total == 0 (no event-outcome pair at this horizon)
    are excluded. Tickers with low n (e.g. < 5) keep their stats —
    the caller (UI) decorates them.
    """
    stmt = (
        select(
            EvaluationEvent.ticker,
            EvaluationEvent.subtype,
            EvaluationOutcome.excess_return,
        )
        .join(EvaluationOutcome, EvaluationOutcome.event_id == EvaluationEvent.id)
        .where(EvaluationEvent.event_type == "ai_analysis")
        .where(EvaluationOutcome.horizon_trading_days == horizon)
    )
    if subtype is not None:
        stmt = stmt.where(EvaluationEvent.subtype == subtype)
    if source is not None:
        stmt = stmt.where(
            func.json_extract(EvaluationEvent.payload, "$.source") == source,
        )
    if strategy is not None:
        stmt = stmt.where(
            func.json_extract(EvaluationEvent.payload, "$.strategy") == strategy,
        )
    if since is not None:
        stmt = stmt.where(
            EvaluationEvent.event_time >= datetime.combine(since,
                                                            datetime.min.time(),
                                                            tzinfo=UTC),
        )

    by_ticker: dict[str, dict] = {}
    for ticker, sub, excess in db.execute(stmt).all():
        bucket = by_ticker.setdefault(ticker, {"n": 0, "h": 0, "sum": 0.0})
        bucket["n"] += 1
        if _is_hit(sub, excess):
            bucket["h"] += 1
        bucket["sum"] += excess

    rows = [
        TickerHitRate(
            ticker=t,
            n_total=v["n"],
            n_hits=v["h"],
            hit_rate=(v["h"] / v["n"]) if v["n"] > 0 else None,
            avg_excess_return=(v["sum"] / v["n"]) if v["n"] > 0 else 0.0,
        )
        for t, v in by_ticker.items()
    ]
    rows.sort(key=lambda r: r.hit_rate if r.hit_rate is not None else -1, reverse=True)
    return rows


@dataclass(frozen=True)
class DailyHitRate:
    day: date
    n_total: int
    hit_rate: float | None


def get_hit_rate_trend(
    db: Session,
    *,
    horizon: int = 5,
    ticker: str | None = None,
    source: str | None = None,
    strategy: str | None = None,
    subtype: str | None = None,
    since: date | None = None,
    window_days: int = 90,
    rolling: int = 30,
) -> list[DailyHitRate]:
    """Daily rolling hit rate.

    Returns one entry per day in the window (oldest first). For each day,
    the hit_rate is computed over events whose event_time falls in the
    `rolling`-day window ending on that day.
    """
    end = date.today()
    start = since or (end - timedelta(days=window_days - 1))

    stmt = (
        select(
            EvaluationEvent.event_time,
            EvaluationEvent.subtype,
            EvaluationOutcome.excess_return,
        )
        .join(EvaluationOutcome, EvaluationOutcome.event_id == EvaluationEvent.id)
        .where(EvaluationEvent.event_type == "ai_analysis")
        .where(EvaluationOutcome.horizon_trading_days == horizon)
        .where(
            EvaluationEvent.event_time >= datetime.combine(
                start - timedelta(days=rolling),
                datetime.min.time(), tzinfo=UTC,
            ),
        )
    )
    if subtype is not None:
        stmt = stmt.where(EvaluationEvent.subtype == subtype)
    if ticker is not None:
        stmt = stmt.where(EvaluationEvent.ticker == ticker)
    if source is not None:
        stmt = stmt.where(
            func.json_extract(EvaluationEvent.payload, "$.source") == source,
        )
    if strategy is not None:
        stmt = stmt.where(
            func.json_extract(EvaluationEvent.payload, "$.strategy") == strategy,
        )

    raw_rows = db.execute(stmt).all()
    # Bucket events by date
    by_day: dict[date, list[tuple[str, float]]] = {}
    for et, sub, excess in raw_rows:
        d = et.date() if hasattr(et, "date") else et
        by_day.setdefault(d, []).append((sub, excess))

    out: list[DailyHitRate] = []
    cur = start
    while cur <= end:
        window_start = cur - timedelta(days=rolling - 1)
        n = 0
        h = 0
        d2 = window_start
        while d2 <= cur:
            for sub, excess in by_day.get(d2, []):
                n += 1
                if _is_hit(sub, excess):
                    h += 1
            d2 += timedelta(days=1)
        rate = (h / n) if n > 0 else None
        out.append(DailyHitRate(day=cur, n_total=n, hit_rate=rate))
        cur += timedelta(days=1)
    return out


@dataclass(frozen=True)
class EventOutcome:
    event_id: int
    event_time: datetime
    ticker: str
    verdict: str
    source: str
    rationale: str
    horizon: int
    event_price: float
    horizon_price: float
    forward_return: float
    excess_return: float
    hit: bool


def get_recent_events_with_outcomes(
    db: Session,
    *,
    horizon: int = 5,
    ticker: str | None = None,
    source: str | None = None,
    strategy: str | None = None,
    subtype: str | None = None,
    since: date | None = None,
    limit: int = 20,
) -> list[EventOutcome]:
    """Latest events with outcomes at this horizon, newest first."""
    stmt = (
        select(EvaluationEvent, EvaluationOutcome)
        .join(EvaluationOutcome, EvaluationOutcome.event_id == EvaluationEvent.id)
        .where(EvaluationEvent.event_type == "ai_analysis")
        .where(EvaluationOutcome.horizon_trading_days == horizon)
    )
    if subtype is not None:
        stmt = stmt.where(EvaluationEvent.subtype == subtype)
    if ticker is not None:
        stmt = stmt.where(EvaluationEvent.ticker == ticker)
    if source is not None:
        stmt = stmt.where(
            func.json_extract(EvaluationEvent.payload, "$.source") == source,
        )
    if strategy is not None:
        stmt = stmt.where(
            func.json_extract(EvaluationEvent.payload, "$.strategy") == strategy,
        )
    if since is not None:
        stmt = stmt.where(
            EvaluationEvent.event_time >= datetime.combine(
                since, datetime.min.time(), tzinfo=UTC,
            ),
        )
    stmt = stmt.order_by(EvaluationEvent.event_time.desc()).limit(limit)

    out: list[EventOutcome] = []
    for event, outcome in db.execute(stmt).all():
        payload = event.payload or {}
        out.append(EventOutcome(
            event_id=event.id,
            event_time=event.event_time,
            ticker=event.ticker,
            verdict=event.subtype,
            source=payload.get("source", ""),
            rationale=payload.get("rationale", ""),
            horizon=outcome.horizon_trading_days,
            event_price=outcome.event_price,
            horizon_price=outcome.horizon_price,
            forward_return=outcome.forward_return,
            excess_return=outcome.excess_return,
            hit=_is_hit(event.subtype, outcome.excess_return),
        ))
    return out


@dataclass(frozen=True)
class PendingVerdict:
    event_id: int
    event_time: datetime
    ticker: str
    verdict: str
    source: str
    rationale: str
    event_price: float
    horizon: int


def get_pending_verdicts(
    db: Session,
    *,
    horizon: int = 5,
    ticker: str | None = None,
    source: str | None = None,
    strategy: str | None = None,
    subtype: str | None = None,
    since: date | None = None,
    limit: int = 20,
) -> list[PendingVerdict]:
    """Recent ai_analysis verdicts that have NO outcome at this horizon yet.

    Outcome-OPTIONAL (LEFT JOIN + IS NULL): surfaces fresh verdicts before the
    daily outcome-computation job matures them. Does NOT affect hit-rate stats.
    """
    stmt = (
        select(EvaluationEvent)
        .outerjoin(
            EvaluationOutcome,
            and_(
                EvaluationOutcome.event_id == EvaluationEvent.id,
                EvaluationOutcome.horizon_trading_days == horizon,
            ),
        )
        .where(EvaluationEvent.event_type == "ai_analysis")
        .where(EvaluationOutcome.id.is_(None))
    )
    if subtype is not None:
        stmt = stmt.where(EvaluationEvent.subtype == subtype)
    if ticker is not None:
        stmt = stmt.where(EvaluationEvent.ticker == ticker)
    if source is not None:
        stmt = stmt.where(
            func.json_extract(EvaluationEvent.payload, "$.source") == source,
        )
    if strategy is not None:
        stmt = stmt.where(
            func.json_extract(EvaluationEvent.payload, "$.strategy") == strategy,
        )
    if since is not None:
        stmt = stmt.where(
            EvaluationEvent.event_time >= datetime.combine(
                since, datetime.min.time(), tzinfo=UTC,
            ),
        )
    stmt = stmt.order_by(EvaluationEvent.event_time.desc()).limit(limit)

    out: list[PendingVerdict] = []
    for event in db.execute(stmt).scalars().all():
        payload = event.payload or {}
        out.append(PendingVerdict(
            event_id=event.id,
            event_time=event.event_time,
            ticker=event.ticker,
            verdict=event.subtype,
            source=payload.get("source", ""),
            rationale=payload.get("rationale", ""),
            event_price=event.event_price,
            horizon=horizon,
        ))
    return out
