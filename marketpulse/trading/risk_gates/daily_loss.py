"""DailyLossGate — denies OPEN/ADD orders when the day's realized PnL is
at or below -daily_loss_limit. CLOSE/REDUCE bypass (lock 6b-L1).

Boundary semantic: deny when realized_pnl <= -limit (op-test #7)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol

from marketpulse.trading.risk_gate import RiskResult
from marketpulse.trading.risk_gates.config_provider import DailyLossConfig
from marketpulse.trading.types import OrderRequest, RiskIntent

__all__ = ["DailyLossGate", "_TodayRealizedPnlRepo"]


class _TodayRealizedPnlRepo(Protocol):
    def today_realized_pnl(self, *, tick_date: date) -> Decimal: ...


class DailyLossGate:
    name = "daily_loss"

    def __init__(
        self,
        *,
        cfg: DailyLossConfig,
        repository: _TodayRealizedPnlRepo,
    ) -> None:
        self._cfg = cfg
        self._repo = repository

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

        realized = self._repo.today_realized_pnl(
            tick_date=order_request.allocation_date,
        )
        limit = cfg.daily_loss_limit
        if realized <= -limit:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="daily_loss_limit_exceeded",
                context={
                    "today_realized_pnl": str(realized),
                    "daily_loss_limit": str(limit),
                    "allocation_date": order_request.allocation_date.isoformat(),
                },
            )
        return RiskResult(approved=True, gate_name=self.name, reason="")
