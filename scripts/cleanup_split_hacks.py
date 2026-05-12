#!/usr/bin/env python3
"""One-off migration: convert pre-feature 'price=0 buy with 拆股 in notes'
Trade rows into proper StockSplit rows.

Idempotent — uses (ticker, ex_date) uniqueness on stock_splits; rows already
migrated are skipped.

Usage:
    DB_URL="sqlite:///./data/marketpulse.db" python scripts/cleanup_split_hacks.py

After verifying output, delete this script — it's not meant to live in the repo.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Ensure project root is on sys.path when script is run directly (e.g. via
# `python scripts/cleanup_split_hacks.py`) without an editable install.
sys.path.insert(0, str(Path(__file__).parent.parent))

from marketpulse.db import base as db_base
from marketpulse.db.models import Trade
from marketpulse.holdings.splits import SplitError, record_split
from marketpulse.holdings.trades import recompute_ticker
from marketpulse.logging import configure_logging, get_logger

log = get_logger(__name__)


def main() -> int:
    configure_logging("INFO")
    db_url = os.environ.get("DB_URL")
    if not db_url:
        print("DB_URL env var required", file=sys.stderr)
        return 1

    db_base.init_engine(db_url)
    gen = db_base.session_scope()
    session = next(gen)
    try:
        hack_rows = session.query(Trade).filter(
            Trade.price == 0,
            Trade.notes.like("%拆股%"),
        ).all()

        if not hack_rows:
            print("✓ No hack rows found — nothing to migrate.")
            return 0

        print(f"Found {len(hack_rows)} candidate trade rows to migrate.")
        unparsed: list[int] = []
        migrated = 0
        skipped = 0
        affected_tickers: set[str] = set()

        for t in hack_rows:
            # Parse ratio from notes: supported formats "1:2", "1 → 2", "1拆2", "1-2"
            m = re.search(r"(\d+)\s*[:→拆\-]\s*(\d+)", t.notes or "")
            if m:
                ratio = int(m.group(2)) / int(m.group(1))
            else:
                ratio = 2.0
                unparsed.append(t.id)
                log.warning("split_migration_fallback",
                            trade_id=t.id, notes=t.notes, defaulted_ratio=ratio)

            ex_date = (t.executed_at or t.created_at).date()
            affected_tickers.add(t.ticker)
            try:
                record_split(
                    session, ticker=t.ticker, ex_date=ex_date, ratio=ratio,
                    source="import",
                    notes=f"Migrated from trade #{t.id}: {t.notes or ''}".strip(),
                )
                migrated += 1
            except SplitError as exc:
                log.info("split_migration_already_exists",
                         trade_id=t.id, error=str(exc))
                skipped += 1

            # Delete the hack Trade even on skip — the proper StockSplit already
            # exists (this is an idempotent re-run), so the hack row is redundant.
            session.delete(t)

        session.commit()

        recompute_errors: list[str] = []
        for ticker in affected_tickers:
            try:
                recompute_ticker(session, ticker)
            except Exception as exc:  # noqa: BLE001 — best-effort per ticker
                recompute_errors.append(f"{ticker}: {exc}")
                log.exception("split_migration_recompute_failed", ticker=ticker)

        print(f"\n✓ Migrated {migrated} hack rows, skipped {skipped} duplicates "
              f"(all {len(hack_rows)} hack Trade rows deleted).")
        if unparsed:
            print(f"⚠️  {len(unparsed)} rows used the default 2.0 ratio because "
                  f"the notes didn't parse. Trade IDs: {unparsed}")
            print("   Review each and POST /splits with the correct ratio if needed.")
        if recompute_errors:
            print(f"\n⚠️  recompute_ticker failed for {len(recompute_errors)} ticker(s):")
            for err in recompute_errors:
                print(f"   - {err}")
            print("   These tickers' Holdings may be stale; run recompute manually.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
