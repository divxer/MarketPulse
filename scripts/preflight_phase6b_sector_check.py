"""Phase 6b preflight check — lock 6b-L11.

Enumerates every distinct (strategy, ticker) pair in paper_position WHERE
status='OPEN', runs `get_sector(t)` on each, and lists tickers that
resolve to None / 'unknown'. Operators MUST add YAML overrides for that
list (or explicitly accept) before flipping the CompositeRiskGate DI seam
into production.

Usage:
    uv run python scripts/preflight_phase6b_sector_check.py [DB_URL]

If DB_URL is omitted, uses MARKETPULSE_DB_URL env var, falling back to
sqlite:///./data/marketpulse.db. Exit code 0 if all OPEN positions have
known sectors, 1 if any unknowns remain.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root is on sys.path when script is run directly (e.g. via
# `uv run python scripts/preflight_phase6b_sector_check.py`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from marketpulse.backtest.sector import get_sector  # noqa: E402
from marketpulse.db.models import PaperPosition  # noqa: E402
from marketpulse.trading.risk_gates._sector import strict_sector  # noqa: E402


def main() -> int:
    db_url = (
        sys.argv[1] if len(sys.argv) > 1
        else os.getenv("MARKETPULSE_DB_URL", "sqlite:///./data/marketpulse.db")
    )
    engine = create_engine(db_url)
    with Session(engine) as session:
        rows = session.execute(
            select(PaperPosition.ticker, PaperPosition.strategy)
            .where(PaperPosition.status == "OPEN")
            .distinct()
        ).all()

    print(f"OPEN paper_position rows: {len(rows)} distinct (strategy, ticker) pairs")
    unknowns: list[tuple[str, str, str]] = []
    for ticker, strategy in rows:
        raw = get_sector(ticker)
        strict = strict_sector(ticker)
        if strict is None:
            unknowns.append((ticker, strategy, raw))

    if not unknowns:
        print("OK: all OPEN positions resolve to a known sector.")
        return 0

    print(f"\nFAIL: {len(unknowns)} ticker(s) resolve to unknown:")
    print(f"  {'ticker':<10} {'strategy':<20} get_sector()")
    print("  " + "-" * 55)
    for ticker, strategy, raw in unknowns:
        print(f"  {ticker:<10} {strategy:<20} {raw!r}")
    print(
        "\nAction (lock 6b-L11): add explicit overrides to "
        "config/sector_overrides.yaml for each ticker above, OR delete the "
        "OPEN positions, OR explicitly accept that pre-6b positions don't "
        "count toward sector caps. Do this BEFORE deploying 6b.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
