# Layer: scheduler
"""Warm the persistent sector cache for the watchlist∪holdings universe.

Network (yfinance) lives HERE, outside the cache-only /watchlist presenter.
Resolution priority (L14): holdings.sector > overrides > cache. Only tickers
unresolved by all three get a yfinance lookup; successes are written to the
persistent sector_cache.json (SECTOR_CACHE_PATH)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketpulse.backtest.sector import (
    load_sector_cache,
    load_sector_overrides,
    save_sector_cache,
)
from marketpulse.db.models import Holding, WatchlistItem
from marketpulse.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class SectorRefreshSummary:
    universe: int
    already: int
    resolved: int
    failed: int


def refresh_sector_cache(db: Session, *, client=None) -> SectorRefreshSummary:
    if client is None:
        from marketpulse.trading.risk_gates._sector import _LazyYfSectorClient

        client = _LazyYfSectorClient()

    watch = {t for (t,) in db.execute(select(WatchlistItem.ticker)).all()}
    holds = {t: s for (t, s) in db.execute(select(Holding.ticker, Holding.sector)).all()}
    universe = sorted(watch | set(holds))

    overrides = load_sector_overrides()
    cache = dict(load_sector_cache())

    already = resolved = failed = 0
    for t in universe:
        if holds.get(t) or t in overrides or t in cache:
            already += 1
            continue
        sector = None
        try:
            sector = client.get_sector(t)
        except Exception as exc:  # noqa: BLE001 — never crash the job
            log.warning("sector_refresh_fetch_failed", ticker=t, error=str(exc))
        if sector:
            cache[t] = sector
            resolved += 1
        else:
            failed += 1

    if resolved:
        save_sector_cache(cache)
    log.info(
        "sector_refresh_done",
        universe=len(universe),
        already=already,
        resolved=resolved,
        failed=failed,
    )
    return SectorRefreshSummary(len(universe), already, resolved, failed)
