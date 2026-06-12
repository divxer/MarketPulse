"""FinalizeJob — post-close refresh of provisional price bars (P2 spec §4).

Mounted as the structural step BEFORE the NAV snapshot in the paper-trading
tick (ordering is structural, not clock-based). Also runnable standalone via
python -m marketpulse.cli.finalize_prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketpulse.data.cache import PriceCache
from marketpulse.db.models import PriceCacheEntry
from marketpulse.logging import get_logger
from marketpulse.trading.calendar import NYTradingCalendar

log = get_logger(__name__)

_SPY = "SPY"

# P1 review guard: one dirty ancient provisional row (e.g. a stray 2019 bar)
# must never amplify into a multi-year refetch inside a nightly tick.
MAX_BACKFILL_DAYS = 30


@dataclass(frozen=True)
class FinalizeResult:
    tickers_attempted: int
    bars_finalized: int
    failures: int
    remaining_provisional: int  # provisional rows still left for the selected tickers


def _sessions_back(cal: NYTradingCalendar, d: date, n: int) -> date:
    """Walk back n NY trading sessions from d (exclusive of d itself)."""
    out = d
    for _ in range(n):
        out = out - timedelta(days=1)
        while not cal.is_business_day(out):
            out = out - timedelta(days=1)
    return out


def _provisional_keys(session: Session, tickers: list[str]) -> set[tuple[str, date]]:
    """(ticker, date) keys of provisional rows for the SELECTED tickers only.

    The finalized count must be a LOCAL diff over the tickers this run
    touched — a global before/after count would be polluted by unrelated
    provisional rows (other tickers, outside-window dates) and by SPY's
    forced refresh, misleading later diagnostics.
    """
    rows = session.execute(
        select(PriceCacheEntry.ticker, PriceCacheEntry.date)
        .where(PriceCacheEntry.is_final == False)  # noqa: E712 — SQLA expression
        .where(PriceCacheEntry.ticker.in_(tickers)),
    ).all()
    return {(t, d) for t, d in rows}


def finalize_provisional_bars(
    session: Session,
    *,
    client=None,
    lookback_trading_days: int = 5,
    today_ny: date | None = None,
) -> FinalizeResult:
    """Refresh provisional bars so post-close data flips final (spec §4).

    Ticker selection: tickers with provisional rows dated within the last
    `lookback_trading_days` NY sessions, always unioned with SPY (the
    north-star benchmark leg gets an attempt every run).

    Per-ticker fetch start (explicit branch, review-locked):
        start = earliest provisional date FOR THAT TICKER (any age)
        if the forced ticker has no provisional rows: start = cutoff
    then clamped to today - MAX_BACKFILL_DAYS.
    """
    from marketpulse.data.yfinance_client import YFinanceClient  # lazy: tests inject

    cal = NYTradingCalendar()
    today = today_ny or cal.today_ny_trading_date(datetime.now(UTC))
    cutoff = _sessions_back(cal, today, lookback_trading_days)
    if client is None:
        client = YFinanceClient()

    in_window = session.scalars(
        select(PriceCacheEntry.ticker)
        .where(PriceCacheEntry.is_final == False)  # noqa: E712
        .where(PriceCacheEntry.date >= cutoff)
        .distinct(),
    ).all()
    tickers = sorted(set(in_window) | {_SPY})

    before_keys = _provisional_keys(session, tickers)
    cache = PriceCache(session)
    failures = 0
    for ticker in tickers:
        earliest = session.scalar(
            select(func.min(PriceCacheEntry.date))
            .where(PriceCacheEntry.ticker == ticker)
            .where(PriceCacheEntry.is_final == False),  # noqa: E712
        )
        if earliest is None:  # noqa: SIM108 — explicit branch is review-locked
            start = cutoff           # forced ticker (SPY) with nothing provisional
        else:
            start = min(earliest, cutoff)
        floor = today - timedelta(days=MAX_BACKFILL_DAYS)
        if start < floor:
            log.warning(
                "finalize_backfill_clamped",
                ticker=ticker, requested_start=str(start), clamped_to=str(floor),
            )
            start = floor
        try:
            # fetch_history_range: end is EXCLUSIVE — +1 day includes today.
            bars = client.fetch_history_range(
                ticker, start=start, end=today + timedelta(days=1),
            )
            cache.upsert(ticker, bars)
        except Exception as exc:  # noqa: BLE001 — per-ticker isolation (spec §4.3)
            failures += 1
            if ticker == _SPY:
                # North-star benchmark leg: degradation must be LOUD.
                log.error("finalize_spy_failed", ticker=ticker, error=str(exc))
            else:
                log.warning("finalize_ticker_failed", ticker=ticker, error=str(exc))

    after_keys = _provisional_keys(session, tickers)
    result = FinalizeResult(
        tickers_attempted=len(tickers),
        # Exact local diff: keys that WERE provisional for the selected
        # tickers and no longer are. Immune to unrelated rows and to new
        # bars added by the refresh itself.
        bars_finalized=len(before_keys - after_keys),
        failures=failures,
        remaining_provisional=len(after_keys),
    )
    log.info(
        "finalize_provisional_bars_done",
        tickers_attempted=result.tickers_attempted,
        bars_finalized=result.bars_finalized,
        failures=result.failures,
        remaining_provisional=result.remaining_provisional,
    )
    return result
