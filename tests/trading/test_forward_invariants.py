# Layer: pure
"""6b+T8 / Lock 6b+L16: forward path MUST NOT call PriceProvider before
exit materialization, AND MUST NOT read paper_order.horizon_price for
exit P&L. Enforced via source-level grep so future drift fails at
test time rather than at production exit time."""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (_REPO / path).read_text(encoding="utf-8")


def test_daily_cycle_does_not_call_price_provider():
    """Lock 6b+L16: forward daily_cycle never touches PriceProvider —
    that's the engine's job at exit time."""
    src = _read("marketpulse/trading/daily_cycle.py")
    # No reference to price_provider (snake_case) in any form: not as
    # parameter, not as kwarg, not as comment, not as docstring.
    assert "price_provider" not in src, (
        "daily_cycle.py must not reference price_provider in forward "
        "mode — exit price is fetched by ForwardExecutionEngine at "
        "_materialize_exit time (lock 6b+L16)."
    )
    # The PascalCase Protocol name must also not leak in — daily_cycle
    # has no business with the Protocol at all.
    assert "PriceProvider" not in src, (
        "daily_cycle.py must not import or reference the PriceProvider "
        "Protocol — the engine owns this dependency (lock 6b+L16)."
    )
    # No READ of horizon_price (winner.horizon_price / order.horizon_price /
    # b.horizon_price etc.). The ONE allowed occurrence is the literal
    # OrderRequest kwarg `horizon_price=None,` in _make_order_request — that
    # is a WRITE, not a read, and seals the value to None at the boundary
    # per lock 6b+L1. We deny any other shape of the token.
    lines = src.splitlines()
    allowed_pattern = re.compile(r"^\s*horizon_price=None,\s*(#.*)?$")
    offenders = []
    for i, line in enumerate(lines, start=1):
        if "horizon_price" not in line:
            continue
        if allowed_pattern.match(line):
            continue
        offenders.append((i, line.rstrip()))
    assert not offenders, (
        "daily_cycle.py must not read horizon_price — the field stays "
        "None in forward mode (lock 6b+L1). The only allowed line is the "
        "literal `horizon_price=None,` OrderRequest kwarg. Offenders:\n"
        + "\n".join(f"  L{ln}: {txt}" for ln, txt in offenders)
    )


def test_materialize_exit_calls_close_on_date_exactly_once():
    """Lock 6b+L16: _materialize_exit calls price_provider.close_on_date
    exactly once. AND does NOT read order.horizon_price for P&L."""
    src = _read("marketpulse/trading/forward_engine.py")
    # Extract the _materialize_exit method body. We scan from
    # `def _materialize_exit(` until the next `def ` at the same indent
    # (4 spaces — standard class-method indent).
    m = re.search(
        r"    def _materialize_exit\([^)]*\)[^:]*:.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    assert m is not None, (
        "Could not locate _materialize_exit in forward_engine.py — "
        "regex needs updating after a refactor?"
    )
    body = m.group(0)
    # Exactly one actual call to .close_on_date(...). The audit reason
    # string "close_on_date_returned_none" is a literal description and
    # not a method invocation — we match the call syntax explicitly to
    # disambiguate.
    call_count = body.count(".close_on_date(")
    assert call_count == 1, (
        f"_materialize_exit must call price_provider.close_on_date "
        f"exactly once. Found {call_count} call-site(s)."
    )
    # NEVER read order.horizon_price for exit P&L
    assert "order.horizon_price" not in body, (
        "_materialize_exit must NOT read order.horizon_price for P&L. "
        "Use paper_fill.price as the canonical source (lock 6b+L1)."
    )
