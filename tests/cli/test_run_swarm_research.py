# Layer: cli
"""Phase 8c-1c — run_swarm_research batch CLI + record_event integration.

Covers (plan PR 8c-1c, Step 1):
- records an ai_analysis event with subtype=verdict, payload.strategy=
  "swarm_research", payload.source="swarm", provenance present; reachable via
  evaluation.permutation.load_rows as a swarm_research strategy row.
- abstain (provider returns None) records NO event.
- three-state counts on a mix (one verdict / one None / one price-missing) =
  recorded1 abstained1 failed1.
- price unavailable -> failed, no event.
- config gate: disabled OR empty key -> SystemExit, nothing written.
- secret hygiene: no persisted payload field equals the API key.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from marketpulse.cli.run_swarm_research import main, run_batch
from marketpulse.db.models import (
    EvaluationEvent,
    EvaluationOutcome,
)
from marketpulse.research.swarm_provider import StubSwarmVerdictProvider, SwarmVerdict
from marketpulse.trading.price_provider import ClosePrice

AS_OF = date(2026, 6, 15)


def _close(price: str) -> ClosePrice:
    return ClosePrice(
        price=Decimal(price),
        price_date=AS_OF,
        requested_date=AS_OF,
        source="stub",
    )


class _StubPriceProvider:
    """Tiny stub exposing close_on_date(*, ticker, on_date) -> obj with .price."""

    def __init__(self, prices: dict[str, ClosePrice]) -> None:
        self._prices = prices

    def close_on_date(self, *, ticker: str, on_date: date):  # noqa: ARG002
        return self._prices.get(ticker.strip().upper())


def _verdict(label: str, *, api_key: str = "tok") -> SwarmVerdict:
    # Provenance mirrors 8c-1b shape: host only, NO token field.
    return SwarmVerdict(
        verdict=label,
        run_id="run-1",
        provenance={"host": "nas.local", "backend": "qwen", "preset": "default"},
    )


# --------------------------------------------------------------------------
# run_batch-level tests (direct db_session)
# --------------------------------------------------------------------------

def test_records_ai_analysis_event_reachable_by_load_rows(db_session: Session):
    """# Layer: behavioral — recorded verdict is a swarm_research strategy row."""
    provider = StubSwarmVerdictProvider({"AAPL": _verdict("bullish")})
    price_provider = _StubPriceProvider({"AAPL": _close("190.00")})

    res = run_batch(
        db_session, tickers=["AAPL"], as_of=AS_OF,
        provider=provider, price_provider=price_provider,
    )
    db_session.commit()

    assert res.recorded == 1
    assert res.abstained == 0
    assert res.failed == 0

    ev = db_session.execute(select(EvaluationEvent)).scalar_one()
    assert ev.event_type == "ai_analysis"
    assert ev.subtype == "bullish"
    assert ev.ticker == "AAPL"
    assert ev.event_price == 190.0
    # record_event was passed a tz-aware UTC EOD datetime (debug log shows
    # +00:00); SQLite drops tzinfo on read-back, so compare the wall value.
    assert ev.event_time.replace(tzinfo=None) == datetime(2026, 6, 15, 0, 0)
    assert ev.payload["source"] == "swarm"
    assert ev.payload["strategy"] == "swarm_research"
    assert ev.payload["provenance"] == {
        "host": "nas.local", "backend": "qwen", "preset": "default",
    }

    # Reachable by load_rows as a swarm_research strategy row: seed an outcome
    # (load_rows joins EvaluationOutcome) then confirm the strategy label flows.
    db_session.add(EvaluationOutcome(
        event_id=ev.id,
        horizon_trading_days=5,
        event_price=190.0,
        horizon_price=200.0,
        horizon_date=date(2026, 6, 22),
        forward_return=0.05,
        benchmark_ticker="SPY",
        benchmark_forward_return=0.01,
        excess_return=0.04,
    ))
    db_session.commit()

    from marketpulse.evaluation.permutation import load_rows
    rows = load_rows(db_session, horizon=5)
    assert ("bullish", pytest.approx(0.04), "swarm_research") in rows


def test_abstain_records_no_event(db_session: Session):
    """# Layer: behavioral — provider returns None -> 0 events for that ticker."""
    provider = StubSwarmVerdictProvider({})  # NVDA absent -> None
    price_provider = _StubPriceProvider({"NVDA": _close("120.00")})

    res = run_batch(
        db_session, tickers=["NVDA"], as_of=AS_OF,
        provider=provider, price_provider=price_provider,
    )
    db_session.commit()

    assert res.abstained == 1
    assert res.recorded == 0
    assert res.failed == 0
    count = db_session.execute(
        select(func.count()).select_from(EvaluationEvent)
    ).scalar_one()
    assert count == 0


def test_three_state_counts_on_mix(db_session: Session):
    """# Layer: behavioral — one verdict / one None / one price-missing =
    recorded1 abstained1 failed1 (Protection 3)."""
    provider = StubSwarmVerdictProvider({
        "AAPL": _verdict("bullish"),   # recorded
        # MSFT absent -> abstained
        "TSLA": _verdict("bearish"),   # price missing -> failed
    })
    price_provider = _StubPriceProvider({
        "AAPL": _close("190.00"),
        # TSLA: no price -> failed
    })

    res = run_batch(
        db_session, tickers=["AAPL", "MSFT", "TSLA"], as_of=AS_OF,
        provider=provider, price_provider=price_provider,
    )
    db_session.commit()

    assert res.recorded == 1
    assert res.abstained == 1
    assert res.failed == 1
    # Only the recorded ticker has an event.
    tickers = db_session.execute(
        select(EvaluationEvent.ticker)
    ).scalars().all()
    assert tickers == ["AAPL"]


def test_price_unavailable_is_failed_no_event(db_session: Session):
    """# Layer: behavioral — had a verdict but no price -> failed, no event."""
    provider = StubSwarmVerdictProvider({"AAPL": _verdict("neutral")})
    price_provider = _StubPriceProvider({})  # no price for AAPL

    res = run_batch(
        db_session, tickers=["AAPL"], as_of=AS_OF,
        provider=provider, price_provider=price_provider,
    )
    db_session.commit()

    assert res.failed == 1
    assert res.recorded == 0
    assert res.abstained == 0
    count = db_session.execute(
        select(func.count()).select_from(EvaluationEvent)
    ).scalar_one()
    assert count == 0


# --------------------------------------------------------------------------
# main()-level tests (config gate + secret hygiene + happy path)
# --------------------------------------------------------------------------

def _init_test_db(db_url: str):
    from marketpulse.db import base as db_base
    from marketpulse.db.base import Base
    db_base.init_engine(db_url)
    Base.metadata.create_all(db_base.get_engine())
    return db_base


def _enable_swarm(monkeypatch, *, enabled: bool, api_key: str) -> None:
    monkeypatch.setenv("SWARM_RESEARCH_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("SWARM_RESEARCH_API_KEY", api_key)
    from marketpulse.config import get_settings
    get_settings.cache_clear()


def test_config_gate_disabled_exits_nonzero_writes_nothing(
    db_url: str, monkeypatch,
):
    """# Layer: behavioral — ENABLED=false -> SystemExit, nothing written."""
    db_base = _init_test_db(db_url)
    monkeypatch.setenv("DATABASE_URL", db_url)
    _enable_swarm(monkeypatch, enabled=False, api_key="tok")

    provider = StubSwarmVerdictProvider({"AAPL": _verdict("bullish")})
    price_provider = _StubPriceProvider({"AAPL": _close("190.00")})

    with pytest.raises(SystemExit) as exc:
        main(["--tickers", "AAPL", "--as-of", "2026-06-15"],
             provider=provider, price_provider=price_provider)
    assert exc.value.code != 0

    engine = create_engine(db_url)
    with Session(engine) as s:
        count = s.execute(
            select(func.count()).select_from(EvaluationEvent)
        ).scalar_one()
    assert count == 0
    db_base.reset_engine()


def test_config_gate_empty_key_exits_nonzero(db_url: str, monkeypatch):
    """# Layer: behavioral — empty API key -> SystemExit, nothing written."""
    db_base = _init_test_db(db_url)
    monkeypatch.setenv("DATABASE_URL", db_url)
    _enable_swarm(monkeypatch, enabled=True, api_key="")

    provider = StubSwarmVerdictProvider({"AAPL": _verdict("bullish")})
    price_provider = _StubPriceProvider({"AAPL": _close("190.00")})

    with pytest.raises(SystemExit) as exc:
        main(["--tickers", "AAPL", "--as-of", "2026-06-15"],
             provider=provider, price_provider=price_provider)
    assert exc.value.code != 0

    engine = create_engine(db_url)
    with Session(engine) as s:
        count = s.execute(
            select(func.count()).select_from(EvaluationEvent)
        ).scalar_one()
    assert count == 0
    db_base.reset_engine()


def test_main_happy_path_records_and_secret_hygiene(db_url: str, monkeypatch):
    """# Layer: behavioral — enabled main() records the event; the persisted
    payload contains NO field equal to the API key (secret hygiene)."""
    api_key = "super-secret-token-123"
    db_base = _init_test_db(db_url)
    monkeypatch.setenv("DATABASE_URL", db_url)
    _enable_swarm(monkeypatch, enabled=True, api_key=api_key)

    provider = StubSwarmVerdictProvider({"AAPL": _verdict("bullish")})
    price_provider = _StubPriceProvider({"AAPL": _close("190.00")})

    main(["--tickers", "AAPL", "--as-of", "2026-06-15"],
         provider=provider, price_provider=price_provider)

    engine = create_engine(db_url)
    with Session(engine) as s:
        ev = s.execute(select(EvaluationEvent)).scalar_one()
        assert ev.subtype == "bullish"
        assert ev.payload["strategy"] == "swarm_research"
        # Secret hygiene: no value anywhere in the payload equals the API key.
        def _no_secret(obj) -> bool:
            if isinstance(obj, dict):
                return all(_no_secret(v) for v in obj.values())
            if isinstance(obj, (list, tuple)):
                return all(_no_secret(v) for v in obj)
            return obj != api_key
        assert _no_secret(ev.payload)
    db_base.reset_engine()
