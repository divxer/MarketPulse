# Layer: cli
"""Manual one-off sector-cache warmup: python -m marketpulse.cli.refresh_sectors"""
from __future__ import annotations

import contextlib

from marketpulse.db.base import session_scope
from marketpulse.scheduler.sector_refresh import refresh_sector_cache


def main() -> None:
    gen = session_scope()
    db = next(gen)
    try:
        s = refresh_sector_cache(db)
        print(
            f"sector warmup: universe={s.universe} already={s.already} "
            f"resolved={s.resolved} failed={s.failed}"
        )
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


if __name__ == "__main__":
    main()
