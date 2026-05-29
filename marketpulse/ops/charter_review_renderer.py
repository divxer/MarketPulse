# Layer: pure
"""PR3b — pure markdown renderer for the weekly charter review.

L9: pure module. No DB, no FS, no clock, no network.
L17: same (payload including generated_at) → byte-identical output.
"""
from __future__ import annotations

from decimal import Decimal

from marketpulse.ops.charter_review_types import (
    CharterReviewPayload,
)

SECTION_SEPARATOR = "\n\n"
REASON_MAX_DISPLAY_LEN = 200
VALUE_NA = "N/A"
DELTA_PRIOR_NA = "prior week N/A"
_MINUS = "−"  # unicode minus sign — typographically matches "+"


def _fmt_pct(value: Decimal | None) -> str:
    """0.032 → '3.2%'; -0.014 → '-1.4%'; None → 'N/A'."""
    if value is None:
        return VALUE_NA
    pct = Decimal(value) * Decimal("100")
    quant = pct.quantize(Decimal("0.1"))
    return f"{quant}%"


def _fmt_int(value: int | None) -> str:
    return VALUE_NA if value is None else f"{int(value)}"


def _fmt_delta_pp(this: Decimal | None, prior: Decimal | None) -> str:
    """Returns '+1.4 pp vs prior week', '−1.8 pp vs prior week',
    or DELTA_PRIOR_NA when prior or this is None."""
    if this is None or prior is None:
        return DELTA_PRIOR_NA
    delta_pp = (Decimal(this) - Decimal(prior)) * Decimal("100")
    delta_pp = delta_pp.quantize(Decimal("0.1"))
    if delta_pp >= 0:
        return f"+{delta_pp} pp vs prior week"
    return f"{_MINUS}{abs(delta_pp)} pp vs prior week"


def _fmt_delta_int(this: int | None, prior: int | None) -> str:
    if this is None or prior is None:
        return DELTA_PRIOR_NA
    delta = int(this) - int(prior)
    if delta >= 0:
        return f"+{delta} vs prior week"
    return f"{_MINUS}{abs(delta)} vs prior week"


def _fmt_index(value: Decimal | None) -> str:
    """L21: raw index multiplier (NOT percent). 1.041 → '1.041'.
    Use for `portfolio_index`, `spy_index`. NEVER use `_fmt_pct` for these —
    that would render 1.041 as '104.1%' which is meaningless."""
    if value is None:
        return VALUE_NA
    quant = Decimal(value).quantize(Decimal("0.001"))
    return f"{quant}"


def _fmt_delta_index(this: Decimal | None, prior: Decimal | None) -> str:
    if this is None or prior is None:
        return DELTA_PRIOR_NA
    delta = (Decimal(this) - Decimal(prior)).quantize(Decimal("0.001"))
    if delta >= 0:
        return f"+{delta} vs prior week"
    return f"{_MINUS}{abs(delta)} vs prior week"


def _fmt_reason(reason: str) -> str:
    """L16 normalization order (locked):
      1. replace any '\\n' or '\\r' with a single space
      2. escape '|' as '\\|' (preserves markdown table grammar)
      3. truncate to REASON_MAX_DISPLAY_LEN chars + '…' if longer

    The aggregator is responsible for converting empty reasons to
    the literal '(no reason)' (L19) BEFORE this function is called.
    """
    normalized = reason.replace("\n", " ").replace("\r", " ")
    escaped = normalized.replace("|", "\\|")
    if len(escaped) > REASON_MAX_DISPLAY_LEN:
        return escaped[:REASON_MAX_DISPLAY_LEN] + "…"
    return escaped


def render_charter_review(*, payload: CharterReviewPayload) -> str:
    """Pure renderer (L9). To be completed in Task 4."""
    raise NotImplementedError("Task 4 wires up section helpers")
