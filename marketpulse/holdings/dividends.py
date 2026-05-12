"""Dividend tracking.

Cash dividends are not Trades — they don't change share count or cost basis.
This module keeps them in their own table and exposes simple aggregates used
by the /holdings dashboard ("累计分红" KPI, monthly dividend rollup).
"""
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketpulse.db.models import Dividend


class DividendError(ValueError):
    """Raised on invalid dividend input or duplicate (ticker, ex_date)."""


def record_dividend(
    session: Session,
    *,
    ticker: str,
    ex_date: date,
    amount_per_share: float,
    total_amount: float,
    source: str = "manual",
    notes: str | None = None,
) -> Dividend:
    """Persist a dividend payout. Commits within. Raises DividendError on
    invalid input or duplicate (ticker, ex_date).
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise DividendError("ticker is required")
    if amount_per_share < 0:
        raise DividendError("amount_per_share cannot be negative")
    if total_amount < 0:
        raise DividendError("total_amount cannot be negative")

    div = Dividend(
        ticker=ticker,
        ex_date=ex_date,
        amount_per_share=amount_per_share,
        total_amount=total_amount,
        source=source,
        notes=notes or None,
    )
    session.add(div)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DividendError(
            f"dividend already recorded for {ticker} on {ex_date}",
        ) from exc
    session.refresh(div)
    return div


def delete_dividend(session: Session, dividend_id: int) -> str:
    """Delete a dividend by id. Returns the affected ticker. Raises
    DividendError if not found.
    """
    div = session.query(Dividend).filter(Dividend.id == dividend_id).one_or_none()
    if not div:
        raise DividendError(f"dividend {dividend_id} not found")
    ticker = div.ticker
    session.delete(div)
    session.commit()
    return ticker


def total_dividends(session: Session, *, ticker: str | None = None) -> float:
    """Sum of all dividends received (optionally filtered by ticker)."""
    q = session.query(Dividend)
    if ticker:
        q = q.filter(Dividend.ticker == ticker.upper())
    return sum(d.total_amount for d in q.all())


def per_ticker_dividends(session: Session) -> dict[str, float]:
    """Map of ticker → total dividends received for that ticker."""
    out: dict[str, float] = defaultdict(float)
    for d in session.query(Dividend).all():
        out[d.ticker] += d.total_amount
    return dict(out)


def monthly_dividends(session: Session) -> list[dict[str, Any]]:
    """Aggregate dividends by (year, month). Same shape as monthly_realized_pl
    so the UI can stack them on the same histogram.
    """
    buckets: dict[str, float] = defaultdict(float)
    for d in session.query(Dividend).all():
        key = f"{d.ex_date.year:04d}-{d.ex_date.month:02d}"
        buckets[key] += d.total_amount
    return [
        {"month": m, "amount": amt}
        for m, amt in sorted(buckets.items())
    ]
