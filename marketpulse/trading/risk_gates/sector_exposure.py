"""SectorExposureGate — denies OPEN/ADD orders whose projected sector
notional exceeds max_sector_exposure_pct * configured_max_capital_in_use.

Lock 6b-L4: denominator is a configured constant (NOT live cash/equity).
Lock 6b-L8: proposed_sector is None → fail-closed deny.

Boundary semantic: projected == cap approves; only projected > cap denies."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Protocol

from marketpulse.trading.risk_gate import RiskResult
from marketpulse.trading.risk_gates.config_provider import SectorExposureConfig
from marketpulse.trading.types import OrderRequest, RiskIntent

__all__ = ["SectorExposureGate", "_SectorExposureRepo"]


class _SectorExposureRepo(Protocol):
    def sector_exposure_notional(
        self,
        *,
        sector_provider: Callable[[str], str | None],
    ) -> dict[str, Decimal]: ...


class SectorExposureGate:
    name = "sector_exposure"

    def __init__(
        self,
        *,
        cfg: SectorExposureConfig,
        repository: _SectorExposureRepo,
        sector_provider: Callable[[str], str | None],
    ) -> None:
        self._cfg = cfg
        self._repo = repository
        self._sector_provider = sector_provider

    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult:
        if order_request.risk_intent in (RiskIntent.CLOSE, RiskIntent.REDUCE):
            return RiskResult(approved=True, gate_name=self.name, reason="")
        if order_request.risk_intent == RiskIntent.FLIP:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="unsupported_risk_intent",
            )

        cfg = self._cfg
        if not cfg.enabled:
            return RiskResult(approved=True, gate_name=self.name, reason="")

        proposed_sector = self._sector_provider(order_request.ticker)
        if proposed_sector is None:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="unknown_sector",
                context={"ticker": order_request.ticker},
            )

        proposed = order_request.event_price * Decimal(order_request.quantity)
        current_by_sector = self._repo.sector_exposure_notional(
            sector_provider=self._sector_provider,
        )
        current = current_by_sector.get(proposed_sector, Decimal(0))
        projected = current + proposed
        cap_dollars = (
            Decimal(str(cfg.max_sector_exposure_pct))
            * cfg.configured_max_capital_in_use
        )
        if projected > cap_dollars:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="sector_cap_exceeded",
                context={
                    "sector": proposed_sector,
                    "current": str(current),
                    "proposed": str(proposed),
                    "projected": str(projected),
                    "cap": str(cap_dollars),
                },
            )
        return RiskResult(approved=True, gate_name=self.name, reason="")
