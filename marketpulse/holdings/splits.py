"""Stock split service layer.

Splits are corporate-action events distinct from Trades. They never modify
Trade rows — `recompute_ticker` applies them on the fly when computing the
current Holding state. See docs/superpowers/specs/2026-05-11-stock-splits-design.md.
"""
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketpulse.db.models import StockSplit


class SplitError(ValueError):
    """Raised on invalid split input or duplicate ex-date for a ticker."""


def record_split(
    session: Session,
    *,
    ticker: str,
    ex_date: date,
    ratio: float,
    source: str = "manual",
    notes: str | None = None,
) -> StockSplit:
    """Persist a stock-split event. Commits within. Raises SplitError on
    invalid input or duplicate (ticker, ex_date).

    Callers that want to recompute the Holding after recording should call
    `marketpulse.holdings.trades.recompute_ticker(session, ticker)` next.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise SplitError("ticker is required")
    if ratio <= 0:
        raise SplitError(f"ratio must be positive, got {ratio}")
    if ratio == 1:
        raise SplitError("ratio of 1 is a no-op; not recording")

    split = StockSplit(
        ticker=ticker,
        ex_date=ex_date,
        ratio=float(ratio),
        source=source,
        notes=notes or None,
    )
    session.add(split)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise SplitError(
            f"split already recorded for {ticker} on {ex_date}",
        ) from exc
    session.refresh(split)
    return split


def get_splits_for_ticker(session: Session, ticker: str) -> list[StockSplit]:
    """Return all splits for a ticker, ordered by ex_date ascending."""
    return (
        session.query(StockSplit)
        .filter(StockSplit.ticker == ticker.strip().upper())
        .order_by(StockSplit.ex_date.asc())
        .all()
    )


def delete_split(session: Session, split_id: int) -> str:
    """Delete a split by id. Returns the affected ticker so the caller can
    `recompute_ticker` it. Raises SplitError if not found.
    """
    split = session.query(StockSplit).filter(StockSplit.id == split_id).one_or_none()
    if not split:
        raise SplitError(f"split {split_id} not found")
    ticker = split.ticker
    session.delete(split)
    session.commit()
    return ticker
