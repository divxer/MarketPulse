# Layer: cli
"""Manual finalize pass: python -m marketpulse.cli.finalize_prices"""
from __future__ import annotations

import contextlib

from marketpulse.data.finalize import finalize_provisional_bars
from marketpulse.db.base import session_scope


def main() -> None:
    gen = session_scope()
    db = next(gen)
    try:
        r = finalize_provisional_bars(db)
        db.commit()
        print(
            f"finalize: attempted={r.tickers_attempted} "
            f"finalized={r.bars_finalized} failures={r.failures} "
            f"remaining_provisional={r.remaining_provisional}"
        )
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


if __name__ == "__main__":
    main()
