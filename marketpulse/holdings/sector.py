"""yfinance sector lookup with 24h in-memory cache + DB persistence.

Used by Phase 5d /holdings to populate the sector column.
Bounded backfill keeps the /holdings render path under 6s on cold cache
(3 yfinance calls × ~2s each).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Process-level cache: ticker → (sector_or_None, fetched_at)
_cache: dict[str, tuple[str | None, datetime]] = {}
_TTL = timedelta(hours=24)


def get_sector(ticker: str) -> str | None:
    """Lookup sector from yfinance .info['sector'], cached 24h.

    Returns None when fetch fails or sector key is missing.
    Caller decides whether to fall back to a default label.
    """
    now = datetime.now(UTC)
    cached = _cache.get(ticker)
    if cached and (now - cached[1]) < _TTL:
        return cached[0]
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).info
        sector = info.get("sector") or None
    except Exception:
        sector = None
    _cache[ticker] = (sector, now)
    return sector


def backfill_holding_sectors(
    session: Session,
    *,
    max_per_call: int = 3,
) -> int:
    """Fill Holding.sector for rows where it's NULL. Bounded + idempotent.

    yfinance .info is ~1-3s per ticker. To avoid blocking the /holdings
    render path for tens of seconds on first load, we cap to `max_per_call`
    per request. Subsequent renders pick up the next batch. After
    ceil(N/max_per_call) page visits all rows are filled.

    Returns count of rows newly filled.
    """
    from marketpulse.db.models import Holding

    holdings = (
        session.query(Holding)
        .filter(Holding.sector.is_(None))
        .limit(max_per_call)
        .all()
    )
    n = 0
    for h in holdings:
        sec = get_sector(h.ticker)
        if sec:
            h.sector = sec
            n += 1
    if n > 0:
        session.commit()
    return n
