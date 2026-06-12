# Layer: cli
"""Independent pricing audit: python -m marketpulse.cli.pricing_audit

NOT broker shadow. Compares paper fills + the NAV series against Tencent
(independent vendor). Pre-registered thresholds; deliberately NO flags to
override them (a FAIL is a FAIL).
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import sys

from marketpulse.data.tencent_client import TencentClient
from marketpulse.db.base import session_scope
from marketpulse.evaluation.pricing_audit import (
    AuditBar,
    load_fills,
    load_nav_days,
    run_pricing_audit,
)


def _json_default(o):
    import datetime
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()
    raise TypeError(type(o).__name__)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="60d", help="tencent history window")
    args = ap.parse_args(argv)  # NO threshold flags by design

    gen = session_scope()
    db = next(gen)
    try:
        fills = load_fills(db)
        nav_days = load_nav_days(db)
        if not fills and not nav_days:
            print("nothing to audit: no fills and no NAV days", file=sys.stderr)
            raise SystemExit(1)
        tickers = (
            {f.ticker for f in fills}
            | {p.ticker for nd in nav_days for p in nd.positions}
            | {"SPY"}
        )
        client = TencentClient()
        bars_by_ticker = {}
        for t in sorted(tickers):
            try:
                bars = client.fetch_history(t, period=args.period)
            except Exception as exc:  # noqa: BLE001 - per-ticker isolation
                print(f"tencent fetch failed for {t}: {exc}", file=sys.stderr)
                bars = []
            bars_by_ticker[t] = [
                AuditBar(date=b.date, open=float(b.open), close=float(b.close))
                for b in bars
            ]
        result = run_pricing_audit(fills, nav_days, bars_by_ticker)
        print(json.dumps(dataclasses.asdict(result), indent=2, default=_json_default))
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


if __name__ == "__main__":
    main()
