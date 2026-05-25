"""Phase 7c - pure position reconciliation."""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from marketpulse.reconcile.types import _SEVERITY_RANK, DiffRow, DiffType


def reconcile_positions(
    paper: Mapping[str, Decimal],
    broker: Mapping[str, Decimal],
) -> list[DiffRow]:
    """Diff two pre-normalized, pre-aggregated symbol-to-quantity maps."""
    rows: list[DiffRow] = []
    # Iterate the union of keysets directly. The final sorted() below
    # imposes the canonical (severity_rank, symbol) order; pre-sorting
    # the union was redundant work.
    for symbol in paper.keys() | broker.keys():
        p = paper.get(symbol)
        b = broker.get(symbol)
        if p is None:
            # Union-membership invariant: at least one side has the
            # symbol, so `b` is guaranteed non-None here.
            assert b is not None
            rows.append(
                DiffRow(
                    symbol=symbol,
                    diff_type=DiffType.MISSING_IN_PAPER,
                    paper_qty=None,
                    broker_qty=b,
                    delta=None,
                    is_red=False,
                )
            )
        elif b is None:
            rows.append(
                DiffRow(
                    symbol=symbol,
                    diff_type=DiffType.MISSING_IN_BROKER,
                    paper_qty=p,
                    broker_qty=None,
                    delta=None,
                    is_red=(p != 0),
                )
            )
        elif p * b < 0:
            rows.append(
                DiffRow(
                    symbol=symbol,
                    diff_type=DiffType.SIDE_MISMATCH,
                    paper_qty=p,
                    broker_qty=b,
                    delta=p - b,
                    is_red=True,
                )
            )
        elif abs(p - b) >= 1:
            rows.append(
                DiffRow(
                    symbol=symbol,
                    diff_type=DiffType.QUANTITY_MISMATCH,
                    paper_qty=p,
                    broker_qty=b,
                    delta=p - b,
                    is_red=False,
                )
            )
        else:
            rows.append(
                DiffRow(
                    symbol=symbol,
                    diff_type=DiffType.MATCHED,
                    paper_qty=p,
                    broker_qty=b,
                    delta=p - b,
                    is_red=False,
                )
            )
    return sorted(rows, key=lambda r: (_SEVERITY_RANK[r.diff_type], r.symbol))
