"""MarketHoursGate — denies OPEN/ADD orders outside the configured NY
placement window. CLOSE/REDUCE bypass (lock 6b-L1). FLIP denies
unsupported_risk_intent."""

from __future__ import annotations

from datetime import time

from marketpulse.trading.calendar import NY, NYTradingCalendar
from marketpulse.trading.clock import Clock
from marketpulse.trading.risk_gate import RiskResult
from marketpulse.trading.risk_gates.config_provider import MarketHoursConfig
from marketpulse.trading.types import OrderRequest, RiskIntent

__all__ = ["MarketHoursGate"]


class MarketHoursGate:
    name = "market_hours"

    def __init__(
        self,
        *,
        cfg: MarketHoursConfig,
        calendar: NYTradingCalendar,
        clock: Clock,
    ) -> None:
        self._cfg = cfg
        self._calendar = calendar
        self._clock = clock

    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult:
        # === RiskIntent bypass/deny (lock 6b-L1) ===
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

        # === Stale allocation_date guard (lock 6b-L7) ===
        today_session = self._calendar.today_ny_trading_date(self._clock.now())
        if order_request.allocation_date != today_session:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="stale_allocation_date",
                context={
                    "allocation_date": order_request.allocation_date.isoformat(),
                    "today_session": today_session.isoformat(),
                },
            )

        # === Session-day guard ===
        if not self._calendar.is_business_day(order_request.allocation_date):
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="not_a_session_day",
                context={"allocation_date": order_request.allocation_date.isoformat()},
            )

        # === Wall-time window check ===
        now_ny = self._clock.now().astimezone(NY)
        if not _window_check(now_ny.time(), cfg):
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="outside_placement_window",
                context={"now_ny": now_ny.isoformat()},
            )
        return RiskResult(approved=True, gate_name=self.name, reason="")


def _window_check(t: time, cfg: MarketHoursConfig) -> bool:
    """Returns True iff t falls within any enabled NY-time window.
    Boundaries:
      - premarket:        [04:00, 09:30)  inclusive-left, exclusive-right
      - regular session:  [09:30, 16:00]  inclusive both ends
      - post-close:       (16:00, post_close_until]  exclusive-left,
                                                     inclusive-right
    If all flags False → False (no valid placement window)."""
    if cfg.allow_premarket and time(4, 0) <= t < time(9, 30):
        return True
    if cfg.allow_regular_session and time(9, 30) <= t <= time(16, 0):
        return True
    # Keep as explicit early-return for parity with the other windows.
    if cfg.allow_post_close and time(16, 0) < t <= cfg.post_close_until:  # noqa: SIM103
        return True
    return False
