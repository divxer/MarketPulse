# Layer: cli
"""Permutation test: python -m marketpulse.cli.permutation_test [--horizon 5]
[--permutations 10000] [--seed 42] [--alpha 0.05]"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import sys

from marketpulse.db.base import session_scope
from marketpulse.evaluation.permutation import load_rows, run_permutation_test


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--permutations", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--alpha", type=float, default=0.05)
    args = ap.parse_args(argv)

    gen = session_scope()
    db = next(gen)
    try:
        rows = load_rows(db, horizon=args.horizon)
        if not rows:
            print(f"no rows: no resolved h{args.horizon} outcomes yet", file=sys.stderr)
            raise SystemExit(1)
        result = run_permutation_test(
            rows,
            n_permutations=args.permutations,
            seed=args.seed,
            alpha=args.alpha,
        )
        out = {"horizon": args.horizon, **dataclasses.asdict(result)}
        print(json.dumps(out, indent=2))
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


if __name__ == "__main__":
    main()
