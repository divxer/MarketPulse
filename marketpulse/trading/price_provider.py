"""PriceProvider Protocol + ClosePrice + reference implementations.

Phase 6b+ (paper P&L realization):
- `ClosePrice` dataclass carries provenance (requested_date vs price_date
  for roll-back transparency; source for audit).
- `PriceProvider.close_on_date(ticker, on_date)` returns the most recent
  available close at or before `on_date`. None means "no data in window."
- Providers expose `source: str` and `lookback_days: int` properties so
  audit rows can record provenance from the actual provider, not
  hardcoded values (lock 6b+L8).
- `YFinancePriceProvider` lives in this module and wraps a new
  `YFinanceClient.fetch_close_on_date` method (added in T4).
- `StubPriceProvider` is test-only: NO `default` parameter; only exact
  map lookup; miss returns None (lock 6b+L3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Protocol

LOOKBACK_DAYS = 10
"""Default calendar-day window for YFinancePriceProvider.

Covers any US holiday cluster (Thanksgiving + adjacent weekend ≈ 5
non-trading days; 10-day window adds safety margin)."""

_QUANT = Decimal("0.000001")
"""Lock 6b+L14: 6 decimal places matches paper_fill.price Numeric(18, 6).
HALF_EVEN rounding aligns with Python's default banker's rounding for
floats and is deterministic across platforms."""


@dataclass(frozen=True)
class ClosePrice:
    """A close price for a (ticker, on_date) query.

    `price_date` is the actual date of the bar yfinance returned. It can
    differ from `requested_date` when the requested date is non-session
    (roll-back to previous available close — see spec § 2).

    `source` is the provider that produced this (e.g., "yfinance",
    "stub") — used by audit (lock 6b+L8).
    """
    price: Decimal
    price_date: date
    requested_date: date
    source: str


class PriceProvider(Protocol):
    """Lock 6b+L8: providers expose source + lookback_days as properties.
    Audit rows read these directly rather than hardcoding."""

    source: str
    lookback_days: int

    def close_on_date(
        self, *, ticker: str, on_date: date,
    ) -> ClosePrice | None: ...


class YFinancePriceProvider:
    """Production provider. Wraps YFinanceClient.fetch_close_on_date.

    Lock 6b+L14: quantizes the close to 6 decimal places HALF_EVEN before
    constructing ClosePrice. Downstream code (engine, repository) can
    trust the Decimal is round-trip-safe with Numeric(18, 6)."""

    source = "yfinance"

    def __init__(
        self,
        *,
        client,    # YFinanceClient — duck-typed to avoid circular import
        lookback_days: int = LOOKBACK_DAYS,
    ) -> None:
        self._client = client
        self._lookback_days = lookback_days

    @property
    def lookback_days(self) -> int:
        return self._lookback_days

    def close_on_date(
        self, *, ticker: str, on_date: date,
    ) -> ClosePrice | None:
        bar = self._client.fetch_close_on_date(
            ticker, on_date, lookback_days=self._lookback_days,
        )
        if bar is None:
            return None
        price = Decimal(str(bar.close)).quantize(_QUANT, rounding=ROUND_HALF_EVEN)
        return ClosePrice(
            price=price,
            price_date=bar.date,
            requested_date=on_date,
            source=self.source,
        )


class StubPriceProvider:
    """Test-only deterministic provider.

    Lock 6b+L3: NO `default` parameter. Miss returns None. Callers
    responsible for pre-quantizing values in `map`.
    """

    source = "stub"
    lookback_days = 0

    def __init__(
        self,
        *,
        map: dict[tuple[str, date], ClosePrice] | None = None,
    ) -> None:
        self._map: dict[tuple[str, date], ClosePrice] = dict(map or {})

    def close_on_date(
        self, *, ticker: str, on_date: date,
    ) -> ClosePrice | None:
        return self._map.get((ticker, on_date))
