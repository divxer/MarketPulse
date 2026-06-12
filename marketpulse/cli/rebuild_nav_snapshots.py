# Layer: cli
"""Rebuild provisional-contaminated NAV snapshots (P2 spec §6).

python -m marketpulse.cli.rebuild_nav_snapshots
Ordering is FIXED (06-11's north-star depends on prior state):
  1. FinalizeJob heals the bars (SPY 2026-06-10 midday price → true close).
  2. Rebuild 2026-06-10.
  3. Rebuild 2026-06-11.
PaperNavSnapshot Lock L1 names this admin path: is_rebuilt + rebuild_reason.
"""
from __future__ import annotations

import contextlib
from datetime import date

from sqlalchemy import update
from sqlalchemy.orm import Session

from marketpulse.data.finalize import finalize_provisional_bars
from marketpulse.db.base import session_scope
from marketpulse.db.models import PaperNavSnapshot
from marketpulse.logging import get_logger
from marketpulse.portfolio.snapshot_runner import NoCashLedgerForDate, run_nav_snapshot

log = get_logger(__name__)

REBUILD_REASON = "provisional_price_cache_fix"
CONTAMINATED_DATES = (date(2026, 6, 10), date(2026, 6, 11))


def rebuild(session: Session, *, dates: tuple[date, ...] = CONTAMINATED_DATES) -> None:
    # 1. Heal the data first — rebuild order is fixed, not discretionary.
    finalize_provisional_bars(session)
    session.commit()

    # 2./3. Delete + recompute in ascending date order (run_nav_snapshot is
    # idempotent and would otherwise return the stale row without recompute).
    # TRANSACTION-PER-DATE: delete, recompute and flag inside ONE uncommitted
    # transaction — a recompute failure rolls back and RESTORES the old
    # snapshot. Never commit a delete before the replacement exists.
    for d in sorted(dates):
        try:
            deleted = session.query(PaperNavSnapshot).filter(
                PaperNavSnapshot.trading_date == d,
            ).delete()
            run_nav_snapshot(session, trading_date=d)
            session.execute(
                update(PaperNavSnapshot)
                .where(PaperNavSnapshot.trading_date == d)
                .values(is_rebuilt=True, rebuild_reason=REBUILD_REASON),
            )
            session.commit()
            log.info("nav_snapshot_rebuilt", trading_date=str(d), had_existing=bool(deleted))
        except NoCashLedgerForDate:
            session.rollback()  # restores the deleted row — old data never lost
            log.warning("rebuild_skipped_no_ledger", trading_date=str(d))
        except Exception as exc:  # noqa: BLE001 — restore-then-surface
            session.rollback()
            log.error("rebuild_failed", trading_date=str(d), error=str(exc))
            raise


def main() -> None:
    gen = session_scope()
    db = next(gen)
    try:
        rebuild(db)
        print(f"rebuilt {[str(d) for d in CONTAMINATED_DATES]} reason={REBUILD_REASON}")
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


if __name__ == "__main__":
    main()
