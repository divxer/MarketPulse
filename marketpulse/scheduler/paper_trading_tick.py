"""APScheduler entrypoint for the daily paper-trading tick (lock xxv).

This module contains ZERO business logic. It resolves DI and calls
daily_cycle.run."""

from __future__ import annotations

import logging
from decimal import Decimal

from marketpulse.backtest.allocation import allocate_for_day
from marketpulse.db.base import session_scope
from marketpulse.trading import daily_cycle
from marketpulse.trading.bid_aggregator import BidAggregator
from marketpulse.trading.calendar import NYTradingCalendar
from marketpulse.trading.clock import WallClock
from marketpulse.trading.forward_engine import ForwardExecutionEngine
from marketpulse.trading.kill_switch import KillSwitchState
from marketpulse.trading.price_provider import StubPriceProvider
from marketpulse.trading.repository import Repository
from marketpulse.trading.risk_gate import AlwaysApproveRiskGate

log = logging.getLogger(__name__)


def paper_trading_tick_job() -> None:
    gen = session_scope()
    session = next(gen)
    try:
        clock = WallClock()
        calendar = NYTradingCalendar()
        repository = Repository(session=session)
        risk_gate = AlwaysApproveRiskGate()
        kill_switch = KillSwitchState(
            env_var="MP_PAPER_KILL_SWITCH", repository=repository,
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
        )
        log.info(
            "paper_trading_tick done: tick_date=%s placed=%d exits=%d entries=%d errors=%d",
            result.tick_date, result.orders_placed, result.exits_materialized,
            result.entries_materialized, len(result.tick_errors),
        )
    finally:
        session.close()
