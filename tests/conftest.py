import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

# Ensure required env vars exist before importing settings.
os.environ.setdefault("APP_PASSWORD_HASH", "$2b$12$abcdefghijklmnopqrstuv")
os.environ.setdefault("SESSION_SECRET", "x" * 32)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

pytest_plugins = ['pytester']


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
      - >=1 BidRecord with would_change_rank=True (rank flip path exercised)

    Construction: 3 strategies with distinct-shape daily curves (different
    sinusoid phases/amplitudes) and non-uniform bid forward returns per
    strategy. Bids fire every other day over a 60-day window starting day
    30, so by evaluation each strategy has >=30 days of contribution
    history → pool_corr_excluding_self returns non-None. Three strategies
    (not two) are required because pairwise LOO correlations between two
    symmetric strategies are degenerate (identical multipliers, never flip
    rank). contribution_lambda=2.0 amplifies the multiplier spread.

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
    # 3 strategies with DIFFERENT-SHAPED daily curves so leave-one-out
    # contribution correlations diverge per strategy.
    # With 2 symmetric strategies the LOO correlations are pairwise
    # symmetric → identical multipliers → no rank flips. Three curves
    # with distinct noise patterns + non-uniform bid outcomes per
    # strategy produce per-strategy pool_corrs that differ enough to
    # flip the integer rank.
    import math as _math
    a_curve = [
        (base_date + timedelta(days=i),
         10_000.0 * (1.0 + 0.003 * i) * (1.0 + 0.03 * _math.sin(i * 0.5)))
        for i in range(days)
    ]
    b_curve = [
        (base_date + timedelta(days=i),
         10_000.0 * (1.0 + 0.002 * i) * (1.0 + 0.04 * _math.cos(i * 0.7)))
        for i in range(days)
    ]
    c_curve = [
        (base_date + timedelta(days=i),
         10_000.0 * (1.0 + 0.0025 * i) * (1.0 - 0.05 * _math.sin(i * 0.3 + 1.0)))
        for i in range(days)
    ]

    # Non-uniform bid forward returns per strategy so daily PnL series
    # have different shapes (necessary for LOO corr to differ per strategy).
    # Pattern oscillates between profit and loss to add variance.
    bids = []
    for i in range(0, 60, 2):
        bid_date = base_date + timedelta(days=30 + i)
        horizon_date = bid_date + timedelta(days=5)
        # wp_a: alternating big-win / small-win
        a_price = 108.0 if (i // 2) % 2 == 0 else 102.0
        # wp_b: alternating small-win / big-loss
        b_price = 103.0 if (i // 2) % 3 == 0 else 96.0
        # wp_c: alternating loss / win on a different period
        c_price = 97.0 if (i // 2) % 2 == 1 else 107.0
        bids.append(_pair(f"AA{i:02d}", "wp_a", bid_date, 100.0, horizon_date, a_price))
        bids.append(_pair(f"BB{i:02d}", "wp_b", bid_date, 100.0, horizon_date, b_price))
        bids.append(_pair(f"CC{i:02d}", "wp_c", bid_date, 100.0, horizon_date, c_price))

    daily_curves = {"wp_a": a_curve, "wp_b": b_curve, "wp_c": c_curve}

    shared = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=500.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False, correlation_caps_enabled=False,
        contribution_enabled=True,
        contribution_lambda=2.0,
    )
    return {"shared": shared}


# Phase 5e lock #22 — test taxonomy enforcement.
# Phase 5e+ tests MUST include a # Layer: invariant or # Layer: behavioral
# tag in their docstring. The hook fails test collection if any such test
# is missing the tag, preventing taxonomy drift across future phases.

import re  # noqa: E402

_LAYER_TAG_RE = re.compile(r"#\s*Layer:\s*(invariant|behavioral)\b")


def _is_phase5e_or_later_test(item) -> bool:
    """Heuristic: a test belongs to Phase 5e+ if its name contains 'phase5e'
    OR if its function docstring already contains a Layer tag (opt-in by author).
    """
    name = item.name.lower()
    if "phase5e" in name or "phase5d_warm_pool" in name:
        return True
    # If author already wrote a Layer tag, they're opting in to the taxonomy.
    doc = getattr(item.function, "__doc__", None) or ""
    return bool(_LAYER_TAG_RE.search(doc))


def pytest_collection_modifyitems(config, items):
    """Verify every Phase 5e+ test carries a # Layer: tag in its docstring.

    Spec § 2 lock #22. Prevents 'silent taxonomy drift' — when an author
    forgets the tag, the test is silently uncategorized; over time the
    invariant/behavioral discipline decays. This hook makes the failure
    visible at collection time.
    """
    untagged: list[str] = []
    for item in items:
        if not _is_phase5e_or_later_test(item):
            continue
        doc = getattr(item.function, "__doc__", None) or ""
        if not _LAYER_TAG_RE.search(doc):
            untagged.append(item.nodeid)
    if untagged:
        raise pytest.UsageError(
            "Phase 5e+ tests missing required '# Layer: invariant' or "
            "'# Layer: behavioral' tag in docstring:\n  "
            + "\n  ".join(untagged)
        )
