"""Parse Robinhood Account Activity Report CSV into normalized trade rows.

Robinhood's CSV columns:
    Activity Date, Process Date, Settle Date, Instrument, Description,
    Trans Code, Quantity, Price, Amount

We keep only `Trans Code` in {"Buy", "Sell"}. Other codes (ACH, CDIV, SPL, GOLD,
Sweep, MINT, etc.) are ignored for v1 — dividends/splits are tracked separately
when we extend the Trade model.

Price/Amount columns are formatted like `$123.45` or `($123.45)` for negatives.
Quantity may include a trailing `S` for shares; we strip it.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class ParsedTrade:
    ticker: str
    action: str  # "buy" | "sell"
    quantity: float
    price: float
    executed_at: datetime
    raw_row: int  # 1-based row index in the source CSV (for error reporting)


class RobinhoodParseError(ValueError):
    """Raised when the CSV is missing required columns or a row is malformed."""


_REQUIRED_COLUMNS = ("Activity Date", "Instrument", "Trans Code", "Quantity", "Price")
_MONEY_RE = re.compile(r"[\$,\s]")


def _parse_money(s: str) -> float:
    s = s.strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = _MONEY_RE.sub("", s)
    val = float(s)
    return -val if neg else val


def _parse_quantity(s: str) -> float:
    s = s.strip().rstrip("S").rstrip("s").strip()
    return float(s) if s else 0.0


def _parse_date(s: str) -> datetime:
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise RobinhoodParseError(f"unrecognized date format: {s!r}")


def parse_robinhood_csv(content: str | bytes) -> list[ParsedTrade]:
    """Parse Robinhood activity CSV. Returns only Buy/Sell rows.

    Skips non-trade rows silently. Raises RobinhoodParseError on bad header
    or malformed Buy/Sell rows (we'd rather fail loudly than mis-import).
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise RobinhoodParseError("CSV is empty or missing header row")

    missing = [c for c in _REQUIRED_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise RobinhoodParseError(f"missing required columns: {missing}")

    out: list[ParsedTrade] = []
    for idx, row in enumerate(reader, start=2):  # row 1 is header
        code = (row.get("Trans Code") or "").strip()
        if code not in ("Buy", "Sell"):
            continue

        ticker = (row.get("Instrument") or "").strip().upper()
        if not ticker:
            raise RobinhoodParseError(f"row {idx}: Buy/Sell with empty Instrument")

        try:
            qty = _parse_quantity(row.get("Quantity") or "")
            price = _parse_money(row.get("Price") or "")
            executed_at = _parse_date(row.get("Activity Date") or "")
        except (ValueError, RobinhoodParseError) as exc:
            raise RobinhoodParseError(f"row {idx} ({code} {ticker}): {exc}") from exc

        if qty <= 0:
            raise RobinhoodParseError(f"row {idx} ({code} {ticker}): non-positive quantity {qty}")
        if price <= 0:
            raise RobinhoodParseError(f"row {idx} ({code} {ticker}): non-positive price {price}")

        out.append(
            ParsedTrade(
                ticker=ticker,
                action="buy" if code == "Buy" else "sell",
                quantity=qty,
                price=price,
                executed_at=executed_at,
                raw_row=idx,
            ),
        )

    return out
