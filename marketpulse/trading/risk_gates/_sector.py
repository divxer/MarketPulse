"""strict_sector wrapper.

marketpulse.backtest.sector.get_sector() always returns str (falls back
to "unknown"). SectorExposureGate needs `None` for the unknown case so
its fail-closed branch (lock 6b-L8) fires. This module bridges the two:

    get_sector("AAPL")   → "Technology"
    get_sector("ZZZZZ")  → "unknown"    (the underlying contract)

    strict_sector("AAPL")  → "Technology"
    strict_sector("ZZZZZ") → None       (gate-friendly contract)

Forward-mode wiring
-------------------
For the live `paper_trading_tick` path, `get_sector` needs a yfinance-
backed `_YfSectorClient` to look up equity tickers not present in
`config/sector_overrides.yaml`. Without it the gate rejects every
equity with `unknown_sector` (observed 2026-05-27: 4/5 daily orders
silently dropped — AMSC / AAPL / AMAT / GOOGL).

This module constructs a lazy default client and persists successful
lookups to `data/sector_cache.json` so subsequent container restarts
don't pay the full yfinance round-trip again.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from contextlib import suppress
from pathlib import Path

from marketpulse.backtest.sector import (
    _SECTOR_CACHE,
    load_sector_cache,
    save_sector_cache,
)
from marketpulse.backtest.sector import (
    get_sector as _get_sector,
)

log = logging.getLogger(__name__)

__all__ = ["safe_sector", "strict_sector"]

_PERSISTED_LOADED = False
_PERSIST_LOCK = threading.Lock()


class _LazyYfSectorClient:
    """yfinance-backed sector lookup mirroring marketpulse.holdings.sector.

    Implements the `_YfSectorClient` Protocol expected by
    backtest/sector.get_sector(yf_client=...). Returns None on any failure
    so the caller's fail-closed path fires. Successful lookups are cached
    in-process via _SECTOR_CACHE (populated by get_sector) and persisted
    to disk by strict_sector / safe_sector after each call.
    """

    def get_sector(self, ticker: str) -> str | None:  # noqa: D401
        try:
            import yfinance as yf
        except ImportError:
            return None
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("strict_sector yfinance_failed ticker=%s err=%s", ticker, exc)
            return None
        sector = info.get("sector") or None
        if not isinstance(sector, str) or not sector:
            return None
        return sector


_YF_CLIENT = _LazyYfSectorClient()
_CACHE_PATH = Path(__file__).parent.parent.parent.parent / "data" / "sector_cache.json"


def _ensure_persisted_loaded() -> None:
    """Lazy-load data/sector_cache.json into _SECTOR_CACHE once per process.

    Survives container restarts: lookups persisted last session avoid the
    yfinance round-trip on first cron tick after restart. The loaded flag
    is set only after a successful read so transient filesystem errors can
    retry on the next call.
    """
    global _PERSISTED_LOADED
    if _PERSISTED_LOADED:
        return
    try:
        loaded = load_sector_cache(_CACHE_PATH)
    except Exception as exc:  # noqa: BLE001
        log.warning("strict_sector cache load failed: %s", exc)
        return
    for t, s in loaded.items():
        _SECTOR_CACHE.setdefault(t, s)
    _PERSISTED_LOADED = True


def _atomic_save_cache() -> None:
    """Persist _SECTOR_CACHE with an atomic same-directory replace."""
    target = _CACHE_PATH
    snapshot = dict(_SECTOR_CACHE)
    with _PERSIST_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".sector_cache.",
            suffix=".json.tmp",
            dir=str(target.parent),
        )
        os.close(tmp_fd)
        try:
            save_sector_cache(snapshot, Path(tmp_path))
            os.replace(tmp_path, target)
        except Exception:
            with suppress(OSError):
                os.unlink(tmp_path)
            raise


def _resolve(ticker: str) -> str:
    _ensure_persisted_loaded()
    before = _SECTOR_CACHE.get(ticker)
    s = _get_sector(ticker, yf_client=_YF_CLIENT)
    # Persist when a NEW successful lookup landed in the cache (yfinance
    # populated _SECTOR_CACHE on success). Skip when no change to avoid
    # disk thrash on every gate call.
    after = _SECTOR_CACHE.get(ticker)
    if before is None and after is not None:
        try:
            _atomic_save_cache()
        except Exception as exc:  # noqa: BLE001
            log.warning("strict_sector cache save failed: %s", exc)
    return s


def strict_sector(ticker: str) -> str | None:
    s = _resolve(ticker)
    if not s or s == "unknown":
        return None
    return s


def safe_sector(ticker: str) -> str:
    """Allocator-facing sector provider using the same resolution chain.

    The allocator wants a string bucket, while the gate wants None to
    fail closed. Keep both paths on the same YAML/cache/yfinance policy.
    """
    return _resolve(ticker) or "unknown"
