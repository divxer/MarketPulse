"""PriceProvider Protocol — daily_cycle uses an injected provider to
fill OrderRequest.horizon_price BEFORE construction. With horizon_price
populated, ForwardExecutionEngine.tick(horizon) will not raise
ENGINE_INVARIANT_ERROR in the normal forward flow.

6a ships StubPriceProvider for tests. Production wires a real
provider (yfinance-backed or broker quote API) — out of scope for the
6a foundation. The seam exists in marketpulse/scheduler/
paper_trading_tick.py; replace StubPriceProvider with a real
implementation when shipping."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol


class PriceProvider(Protocol):
    def horizon_price(self, *, ticker: str, horizon_date: date) -> Decimal | None: ...


class StubPriceProvider:
    """Deterministic test provider. Configured via an exact lookup map
    and/or a default fallback. Returns None when neither matches."""

    def __init__(
        self,
        *,
        map: dict[tuple[str, date], Decimal] | None = None,
        default: Decimal | None = None,
    ) -> None:
        self._map = map or {}
        self._default = default

    def horizon_price(self, *, ticker: str, horizon_date: date) -> Decimal | None:
        return self._map.get((ticker, horizon_date), self._default)
