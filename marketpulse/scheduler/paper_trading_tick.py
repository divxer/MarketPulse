"""APScheduler entrypoint for the daily paper-trading tick (lock xxv).

This module is the **composition root** for Phase 6b risk gates (lock
6b-L15). It owns the canonical 4-gate composite by calling
`build_standard_composite(...)` — no business logic, just DI wiring +
delegation to daily_cycle.run.

Phase 6b: AlwaysApproveRiskGate → CompositeRiskGate (4 production gates).
RiskConfigProvider reads config/risk_gates.yaml + per-strategy `risk:`
blocks from marketpulse/strategies/definitions/*.yaml."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

from marketpulse.backtest.allocation import allocate_for_day
from marketpulse.backtest.sector import get_sector
from marketpulse.db.base import session_scope
from marketpulse.trading import daily_cycle
from marketpulse.trading.bid_aggregator import BidAggregator
from marketpulse.trading.calendar import NYTradingCalendar
from marketpulse.trading.clock import WallClock
from marketpulse.trading.forward_engine import ForwardExecutionEngine
from marketpulse.trading.kill_switch import KillSwitchState
from marketpulse.trading.price_provider import StubPriceProvider
from marketpulse.trading.repository import Repository
from marketpulse.trading.risk_gates import (
    RiskConfigProvider,
    build_standard_composite,
    strict_sector,
)

log = logging.getLogger(__name__)

# Resolve config paths once at import — these are deployment-static.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_RISK_GATES_YAML = _REPO_ROOT / "config" / "risk_gates.yaml"
_STRATEGIES_DIR = _REPO_ROOT / "marketpulse" / "strategies" / "definitions"


def paper_trading_tick_job() -> None:
    gen = session_scope()
    session = next(gen)
    try:
        clock = WallClock()
        calendar = NYTradingCalendar()
        repository = Repository(session=session)

        # Phase 6b: real composite gate replaces the 6a stub. The
        # composition root (this file) owns the canonical gate list via
        # the factory — see lock 6b-L15.
        risk_config_provider = RiskConfigProvider.from_yaml(
            global_path=_RISK_GATES_YAML,
            strategies_dir=_STRATEGIES_DIR,
        )
        kill_switch = KillSwitchState(
            env_var="MP_PAPER_KILL_SWITCH", repository=repository,
        )
        risk_gate = build_standard_composite(
            config_provider=risk_config_provider,
            repository=repository,
            calendar=calendar,
            clock=clock,
            sector_provider=strict_sector,
        )
        engine = ForwardExecutionEngine(
            repository=repository, clock=clock,
            kill_switch=kill_switch, risk_gate=risk_gate,
        )
        bid_aggregator = BidAggregator(session=session, calendar=calendar)
        # TODO(6b/6c): replace StubPriceProvider with a real provider
        # (yfinance-backed or broker quote API). See price_provider.py.
        price_provider = StubPriceProvider(default=Decimal("0"))

        result = daily_cycle.run(
            clock=clock, engine=engine, repository=repository,
            bid_aggregator=bid_aggregator, allocator=allocate_for_day,
            calendar=calendar, kill_switch=kill_switch,
            price_provider=price_provider,
            daily_curves={},
            daily_strategy_contribution_returns={},
            daily_pool_returns=[],
            sector_provider=get_sector,
        )
        log.info(
            "paper_trading_tick done: tick_date=%s placed=%d exits=%d entries=%d errors=%d",
            result.tick_date, result.orders_placed, result.exits_materialized,
            result.entries_materialized, len(result.tick_errors),
        )
    finally:
        session.close()
