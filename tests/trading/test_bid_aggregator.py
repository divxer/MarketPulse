# Layer: behavioral
"""6a-3.1: BidAggregator NY-day window + skip-NULL-strategy."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'ba.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _seed_event(session, *, ticker, event_time, strategy="momentum",
                event_price=150.0, strategy_version="v0"):
    """Seed an EvaluationEvent. EvaluationEvent stores `strategy` /
    `strategy_version` inside the `payload` JSON column (see
    marketpulse/backtest/queries.py for the canonical reader)."""
    from marketpulse.db.models import EvaluationEvent

    payload: dict = {"source": "stock_analysis"}
    if strategy is not None:
        payload["strategy"] = strategy
    if strategy_version is not None:
        payload["strategy_version"] = strategy_version

    e = EvaluationEvent(
        event_type="ai_analysis",
        subtype="bullish",
        ticker=ticker,
        event_time=event_time,
        event_price=event_price,
        payload=payload,
    )
    session.add(e)
    session.commit()
    return e


def test_collect_for_date_returns_today_events_only(session):
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar

    # Yesterday's event (14:00 NY 2026-05-20 = 18:00 UTC on 2026-05-20)
    _seed_event(session, ticker="AAPL",
                event_time=datetime(2026, 5, 20, 18, 0, tzinfo=UTC))
    # Today's event (10:00 NY 2026-05-21 = 14:00 UTC on 2026-05-21)
    _seed_event(session, ticker="MSFT",
                event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC))

    agg = BidAggregator(session=session, calendar=NYTradingCalendar())
    bids = agg.collect_for_date(date(2026, 5, 21))
    tickers = {b.ticker for b in bids}
    assert tickers == {"MSFT"}, f"expected today-only events; got {tickers}"


def test_collect_skips_events_with_null_strategy(session):
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar

    _seed_event(session, ticker="OK",
                event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
                strategy="momentum")
    _seed_event(session, ticker="NO_STRAT",
                event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
                strategy=None)

    agg = BidAggregator(session=session, calendar=NYTradingCalendar())
    bids = agg.collect_for_date(date(2026, 5, 21))
    tickers = {b.ticker for b in bids}
    assert tickers == {"OK"}


def test_collect_skips_research_only_events(session):
    """Critical isolation: a research_only event (e.g. swarm_research) carries a
    strategy label for the permutation pipeline but MUST NOT become a bid."""
    from marketpulse.db.models import EvaluationEvent
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar

    # executable arm — collected
    _seed_event(session, ticker="OK",
                event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
                strategy="momentum")
    # research-only swarm arm, same day, has a strategy — must be EXCLUDED
    session.add(EvaluationEvent(
        event_type="ai_analysis", subtype="bullish", ticker="SWARM",
        event_time=datetime(2026, 5, 21, 14, 5, tzinfo=UTC), event_price=150.0,
        payload={"source": "swarm", "strategy": "swarm_research",
                 "research_only": True, "provenance": {"engine": "vibe-trading"}},
    ))
    session.commit()

    agg = BidAggregator(session=session, calendar=NYTradingCalendar())
    tickers = {b.ticker for b in agg.collect_for_date(date(2026, 5, 21))}
    assert tickers == {"OK"}, f"research_only must not become a bid; got {tickers}"
