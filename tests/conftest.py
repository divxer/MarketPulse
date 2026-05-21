import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

# Ensure required env vars exist before importing settings.
os.environ.setdefault("APP_PASSWORD_HASH", "$2b$12$abcdefghijklmnopqrstuv")
os.environ.setdefault("SESSION_SECRET", "x" * 32)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _clear_quote_cache() -> None:
    """The QUOTE_CACHE module-level singleton must not leak across tests."""
    from marketpulse.data.quote_cache import QUOTE_CACHE
    QUOTE_CACHE.clear()
    yield
    QUOTE_CACHE.clear()


@pytest.fixture(autouse=True)
def _clear_phase3_module_caches() -> None:
    """Phase 3 caches (router decisions + loaded strategies) live at module
    level so they survive the per-request AiService instances in production.
    Tests need them cleared between cases.
    """
    from marketpulse.ai.service import _router_cache_clear
    from marketpulse.strategies.loader import clear_strategy_cache
    _router_cache_clear()
    clear_strategy_cache()
    yield
    _router_cache_clear()
    clear_strategy_cache()


@pytest.fixture(scope="session", autouse=True)
def _ensure_tailwind_output_exists():
    """Ensure marketpulse/web/static/app.css exists before tests run.

    Tailwind output is .gitignore'd; on a fresh checkout or in CI
    without node, the file is missing. Tests that assert on
    static_version('app.css') need *some* file there. We create a
    minimal stub if the real build hasn't produced one; a real
    Tailwind run (npm run build:css) will overwrite it.
    """
    css_path = (
        Path(__file__).resolve().parent.parent
        / "marketpulse" / "web" / "static" / "app.css"
    )
    if not css_path.exists():
        css_path.write_text(
            "/* tailwind build stub — run `npm run build:css` for the real one */\n"
        )
    yield


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture()
def db_session(db_url: str) -> Session:
    from marketpulse.db import base as db_base
    from marketpulse.db.base import Base

    db_base.init_engine(db_url)
    Base.metadata.create_all(db_base.get_engine())
    gen = db_base.session_scope()
    session = next(gen)
    try:
        yield session
    finally:
        session.close()
        db_base.reset_engine()


@pytest.fixture()
def client(db_url: str):
    from fastapi.testclient import TestClient

    from marketpulse.db import base as db_base
    from marketpulse.db.base import Base
    from marketpulse.web.main import create_app

    db_base.init_engine(db_url)
    Base.metadata.create_all(db_base.get_engine())
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def phase5d_warm_pool():
    """Phase 5e Thread B fixture (spec § 2 lock #9).

    Produces a backtest result with:
      - >=1 bid carrying non-None pool_corr (warm-up complete)
      - >=1 strategy showing non-zero rank_drift_from_signal (rank flip path executed)

    Construction: 2 strategies with anti-correlated daily curves, bids on
    every other day over a 60-day window starting from day 30 (so by the time
    a bid is evaluated, the strategy has >=30 days of contribution-return
    history -> pool_corr_excluding_self returns non-None).

    Returns the dict from simulate_shared_pool. The 'shared' key holds
    the PortfolioBacktestResult.
    """
    from dataclasses import dataclass
    from datetime import UTC, date, datetime, timedelta

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    @dataclass(frozen=True)
    class _BidInput:
        strategy: str
        ticker: str
        event_time: datetime
        event_price: float
        horizon_price: float
        horizon_date: date
        forward_return: float
        benchmark_forward_return: float

    def _pair(ticker, strategy, event_date, event_price, horizon_date,
              horizon_price, benchmark_return=0.01):
        return _BidInput(
            strategy=strategy, ticker=ticker,
            event_time=datetime.combine(event_date, datetime.min.time(), tzinfo=UTC),
            event_price=event_price, horizon_price=horizon_price,
            horizon_date=horizon_date,
            forward_return=(horizon_price - event_price) / event_price,
            benchmark_forward_return=benchmark_return,
        )

    base_date = date(2026, 1, 1)
    days = 120
    # Strategy A: monotone growth
    a_curve = [
        (base_date + timedelta(days=i), 10_000.0 * (1.005 ** i))
        for i in range(days)
    ]
    # Strategy B: anti-correlated zigzag riding the same growth trajectory
    b_curve = [
        (base_date + timedelta(days=i),
         10_000.0 * (1.005 ** i) * (1.0 - 0.005 * (i % 2)))
        for i in range(days)
    ]

    bids = []
    for i in range(0, 60, 2):
        bid_date = base_date + timedelta(days=30 + i)
        horizon_date = bid_date + timedelta(days=5)
        bids.append(_pair(f"AA{i:02d}", "wp_a", bid_date, 100.0, horizon_date, 105.0))
        bids.append(_pair(f"BB{i:02d}", "wp_b", bid_date, 100.0, horizon_date, 105.0))

    daily_curves = {"wp_a": a_curve, "wp_b": b_curve}

    shared = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=500.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False, correlation_caps_enabled=False,
        contribution_enabled=True,
        contribution_lambda=1.0,
    )
    return {"shared": shared}
