# Layer: invariant
"""6a-3.4: paper_trading_tick.py is THIN (lock xxv)."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_network_finalize(monkeypatch):
    """P2F-T6: the tick now runs finalize_provisional_bars before the NAV
    snapshot; no-op it so the DI tests below never reach yfinance."""
    import marketpulse.scheduler.paper_trading_tick as m

    monkeypatch.setattr(m, "finalize_provisional_bars", lambda session: None)


def test_scheduler_entrypoint_is_thin():
    """No SQL, no business logic, no state mutation inside the scheduler
    entrypoint. It must only resolve DI and call daily_cycle.run."""
    src = Path("marketpulse/scheduler/paper_trading_tick.py").read_text()

    # Forbid SQL fragments and direct paper_* writes.
    forbidden = [
        "session.add", "session.execute(insert", "session.execute(update",
        "INSERT", "UPDATE", "DELETE",
    ]
    for f in forbidden:
        assert f not in src, (
            f"thin-wrapper violation: '{f}' in scheduler entrypoint"
        )

    # The file should be small.
    line_count = len([
        line for line in src.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ])
    # Phase 6b raised the cap from <60 to <90: the composition-root role
    # (lock 6b-L15) requires constructing 4 risk gates + RiskConfigProvider,
    # which legitimately grows the DI wiring. PR3a raised it from <90 to
    # <92 to accommodate the post-tick NAV snapshot hook
    # (_run_nav_snapshot_safely import + single call). P2F-T6 raised it
    # from <92 to <97 for the finalize-provisional-bars step 0 of the
    # NAV snapshot (import + guarded call). Still small —
    # no business logic.
    assert line_count < 97, (
        f"scheduler entrypoint too thick: {line_count} non-comment lines"
    )

    # Must call daily_cycle.run.
    assert (
        "daily_cycle.run(" in src
        or "from marketpulse.trading import daily_cycle" in src
    )


def test_paper_trading_tick_uses_composite_risk_gate(monkeypatch, tmp_path):
    """6b-T17 DI swap: paper_trading_tick_job must construct a
    CompositeRiskGate (not AlwaysApproveRiskGate)."""
    import marketpulse.scheduler.paper_trading_tick as m
    from marketpulse.db import base as db_base
    from marketpulse.db.base import Base
    from marketpulse.trading.risk_gates.composite import CompositeRiskGate

    captured = {}

    real_engine_cls = m.ForwardExecutionEngine

    class _SpyEngine(real_engine_cls):
        def __init__(self, *args, **kwargs):
            captured["risk_gate"] = kwargs.get("risk_gate")
            super().__init__(*args, **kwargs)

        def tick(self, *, as_of):
            from marketpulse.trading.types import TickResult
            return TickResult(as_of=as_of, entries_materialized=0,
                              exits_materialized=0, errors=())

    monkeypatch.setattr(m, "ForwardExecutionEngine", _SpyEngine)

    # Set up an isolated per-test SQLite DB with the paper_* tables created
    # so the real session_scope context inside paper_trading_tick_job works.
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    db_base.init_engine(db_url)
    Base.metadata.create_all(db_base.get_engine())
    try:
        m.paper_trading_tick_job()
    finally:
        db_base.reset_engine()

    assert isinstance(captured["risk_gate"], CompositeRiskGate)


def test_paper_trading_tick_injects_yfinance_price_provider(monkeypatch, tmp_path):
    """T9 DI swap: paper_trading_tick_job must wire YFinancePriceProvider
    into the engine."""
    import marketpulse.scheduler.paper_trading_tick as m
    from marketpulse.trading.price_provider import YFinancePriceProvider

    captured = {}

    real_engine_cls = m.ForwardExecutionEngine

    class _SpyEngine(real_engine_cls):
        def __init__(self, *args, **kwargs):
            captured["price_provider"] = kwargs.get("price_provider")
            super().__init__(*args, **kwargs)

        def tick(self, *, as_of):
            from marketpulse.trading.types import TickResult
            return TickResult(
                as_of=as_of, entries_materialized=0,
                exits_materialized=0, errors=(),
            )

        def last_price_unavailable_count(self) -> int:
            return 0

    monkeypatch.setattr(m, "ForwardExecutionEngine", _SpyEngine)

    # The scheduler job uses session_scope which needs Base.metadata.create_all
    from sqlalchemy import create_engine

    from marketpulse.db import base as db_base
    from marketpulse.db.base import Base
    test_engine = create_engine(f"sqlite:///{tmp_path / 'sch.db'}")
    Base.metadata.create_all(test_engine)
    original_engine = db_base._engine
    db_base._engine = test_engine
    try:
        m.paper_trading_tick_job()
    finally:
        db_base._engine = original_engine

    assert isinstance(captured["price_provider"], YFinancePriceProvider)
