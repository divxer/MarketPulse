# Phase 5d — `/holdings` NineScrolls Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/holdings` page in NineScrolls design language with editorial hero (含 donut), 5-KPI strip, 3-card row (allocation / sector / contributors), 14-column holdings table with sparklines, monthly P&L bars, and HTMX-loaded AI risk card.

**Architecture:** Four-layer stack: new `sector.py` module (yfinance lookup + 24h cache + bounded DB backfill) → extend `service.py` with 3 aggregations + `enrich_holdings` field additions → extend `/holdings` route context + new GET `/holdings/risk-analysis` + new GET `/holdings/export.csv` → rewrite `holdings.html` shell + 8 new partials + CSS.

**Tech Stack:** FastAPI + SQLAlchemy 2.x + Jinja2 + HTMX + vanilla CSS + Material Symbols + Space Grotesk/Inter/Roboto Mono + yfinance + Alembic.

**Spec:** `docs/superpowers/specs/2026-05-15-phase-5d-holdings-page.md`

---

## File Structure

```
marketpulse/
├── db/
│   └── models.py                              MODIFY: add Holding.sector column
├── holdings/
│   ├── sector.py                              NEW: yfinance sector lookup + bounded backfill
│   ├── service.py                             MODIFY: extend enrich_holdings + 3 new aggregations
│   └── ...
├── web/
│   ├── routes/
│   │   └── holdings.py                        MODIFY: route extension + new endpoints
│   ├── static/css/
│   │   └── app.css                            MODIFY: append Phase 5d CSS
│   └── templates/
│       ├── holdings.html                      REWRITE
│       └── partials/
│           ├── holdings_hero.html             NEW
│           ├── holdings_donut.html            NEW
│           ├── holdings_kpi_strip.html        NEW
│           ├── holdings_allocation_card.html  NEW
│           ├── holdings_sector_card.html      NEW
│           ├── holdings_contributors_card.html NEW
│           ├── holdings_table.html            REWRITE (14 cols)
│           ├── holdings_monthly_card.html     NEW
│           └── holdings_risk_card.html        NEW
alembic/versions/
└── <auto>_add_holdings_sector.py              NEW (alembic auto-gen)
tests/
├── holdings/
│   ├── test_sector.py                         NEW
│   └── test_aggregations.py                   EXTEND
└── web/
    ├── test_holdings.py                       EXTEND
    ├── test_holdings_risk.py                  NEW
    └── test_holdings_export.py                NEW
```

---

## Conventions (Applied Throughout)

- **TDD:** Each task writes a failing test first, then implementation, then verifies pass.
- **Jinja format strings:** Use Python `"{:+,.0f}".format(value)` (new-style) — NOT `"%+,.0f"|format(value)` (old-style `%` formatting doesn't support `,` separator).
- **HTMX delete URL:** `/holdings/{int}` — never `/holdings/{ticker}`. Use `r.id` from enriched rows, not `r.ticker`.
- **Models:** `Holding`, `Trade`, `Dividend`, `StockSplit` all in `marketpulse/db/models.py`. Table names are plural: `holdings`, `trades`, `dividends`, `stock_splits`.
- **session_scope() is generator:** Use `next(gen)` pattern if needed; tests use `db_session` fixture from `tests/conftest.py`.
- **Quote dataclass has NO `name` field** (only `ticker / price / change_pct / volume / avg_volume_20d / fetched_at / stale`). For table "名称" column use `r.ticker` as placeholder.
- **Run tests:** `uv run pytest <path> -v`
- **Lint:** `uv run ruff check <path>`
- **Existing function locations:**
  - `enrich_holdings`, `compute_totals`, `allocation_breakdown`, `sort_by_pl_impact`, `monthly_realized_pl`, `trading_stats`, `trade_count_this_month`, `realized_pl_by_ticker`, `avg_hold_days` → `marketpulse/holdings/service.py`
  - `total_realized_pl` → `marketpulse/holdings/trades.py`
  - `monthly_dividends`, `total_dividends`, `per_ticker_dividends` → `marketpulse/holdings/dividends.py`
  - `match_lots_fifo`, `LotMatch` → `marketpulse/holdings/fifo.py`

---

### Task 1: DB migration — add `holdings.sector` column

**Files:**
- Modify: `marketpulse/db/models.py:67` (add `sector` column to `Holding`)
- Create: `alembic/versions/<auto>_add_holdings_sector.py`

- [ ] **Step 1.1: Add `sector` column to `Holding` SQLAlchemy model**

Edit `marketpulse/db/models.py`, find `class Holding(Base):` (line ~67) and add after the existing columns:

```python
sector: Mapped[str | None] = mapped_column(Text, nullable=True)
```

(Place after `avg_cost` and before any sort/timestamp columns. Follow existing column style with `Mapped[...]` annotations.)

- [ ] **Step 1.2: Generate Alembic revision**

```bash
uv run alembic revision -m "add holdings sector"
```

This creates `alembic/versions/<hash>_add_holdings_sector.py` with empty upgrade/downgrade.

- [ ] **Step 1.3: Fill the migration body**

Open the newly created file. Make sure `down_revision = "0df4e23abe4e"` (the latest revision before this one). Replace the upgrade/downgrade functions with:

```python
def upgrade() -> None:
    op.add_column("holdings", sa.Column("sector", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("holdings", "sector")
```

**Critical:** Table name is `"holdings"` (plural), not `"holding"`.

- [ ] **Step 1.4: Run migration**

```bash
uv run alembic upgrade head
```

Expected output: `Running upgrade 0df4e23abe4e -> <hash>, add holdings sector`. No errors.

- [ ] **Step 1.5: Verify schema**

```bash
uv run python -c "from marketpulse.db.models import Holding; print([c.name for c in Holding.__table__.columns])"
```

Expected: list includes `'sector'`.

- [ ] **Step 1.6: Commit**

```bash
git add marketpulse/db/models.py alembic/versions/
git commit -m "feat(db): add Holding.sector column (nullable TEXT)

Lazy-filled by Phase 5d sector module; tolerates NULL (template falls
back to '未分类'). Migration revises 0df4e23abe4e."
```

---

### Task 2: `marketpulse/holdings/sector.py` — yfinance lookup + bounded backfill

**Files:**
- Create: `marketpulse/holdings/sector.py`
- Test: `tests/holdings/test_sector.py` (new)

- [ ] **Step 2.1: Write failing tests**

Create `tests/holdings/test_sector.py`:

```python
"""yfinance sector lookup + bounded backfill."""
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from marketpulse.db.models import Holding


def _holding(session, ticker, *, sector=None) -> Holding:
    h = Holding(ticker=ticker, quantity=1.0, avg_cost=1.0, sort_order=0,
                sector=sector)
    session.add(h)
    session.commit()
    return h


def test_get_sector_returns_yfinance_sector():
    from marketpulse.holdings.sector import _cache, get_sector
    _cache.clear()
    fake_ticker = MagicMock()
    fake_ticker.info = {"sector": "Technology"}
    with patch("yfinance.Ticker", return_value=fake_ticker):
        assert get_sector("AAPL") == "Technology"


def test_get_sector_returns_none_on_failure():
    from marketpulse.holdings.sector import _cache, get_sector
    _cache.clear()
    with patch("yfinance.Ticker", side_effect=RuntimeError("network")):
        assert get_sector("AAPL") is None


def test_get_sector_returns_none_when_field_missing():
    from marketpulse.holdings.sector import _cache, get_sector
    _cache.clear()
    fake_ticker = MagicMock()
    fake_ticker.info = {}  # no sector key
    with patch("yfinance.Ticker", return_value=fake_ticker):
        assert get_sector("AAPL") is None


def test_get_sector_caches_within_ttl():
    """Two calls within TTL → only one yfinance fetch."""
    from marketpulse.holdings.sector import _cache, get_sector
    _cache.clear()
    fake_ticker = MagicMock()
    fake_ticker.info = {"sector": "Technology"}
    with patch("yfinance.Ticker", return_value=fake_ticker) as m:
        get_sector("AAPL")
        get_sector("AAPL")
        assert m.call_count == 1


def test_get_sector_cache_expires_after_ttl():
    """After TTL elapses, next call re-fetches."""
    from marketpulse.holdings import sector as sector_mod
    sector_mod._cache.clear()
    # Insert stale cache entry (25h ago).
    sector_mod._cache["AAPL"] = ("Technology", datetime.now(UTC) - timedelta(hours=25))
    fake_ticker = MagicMock()
    fake_ticker.info = {"sector": "Tech-Refreshed"}
    with patch("yfinance.Ticker", return_value=fake_ticker):
        assert sector_mod.get_sector("AAPL") == "Tech-Refreshed"


def test_backfill_only_fills_null(db_session):
    from marketpulse.holdings.sector import backfill_holding_sectors
    _holding(db_session, "AAPL", sector=None)
    _holding(db_session, "NVDA", sector="Existing")
    with patch("marketpulse.holdings.sector.get_sector", return_value="Technology"):
        n = backfill_holding_sectors(db_session)
    assert n == 1
    db_session.expire_all()
    aapl = db_session.query(Holding).filter_by(ticker="AAPL").one()
    nvda = db_session.query(Holding).filter_by(ticker="NVDA").one()
    assert aapl.sector == "Technology"
    assert nvda.sector == "Existing"  # untouched


def test_backfill_bounded_by_max_per_call(db_session):
    """5 NULL holdings, max_per_call=3 → only 3 filled in one call."""
    from marketpulse.holdings.sector import backfill_holding_sectors
    for t in ("A", "B", "C", "D", "E"):
        _holding(db_session, t, sector=None)
    with patch("marketpulse.holdings.sector.get_sector", return_value="Tech"):
        n = backfill_holding_sectors(db_session, max_per_call=3)
    assert n == 3


def test_backfill_idempotent(db_session):
    """Calling twice after all rows filled returns 0 the second time."""
    from marketpulse.holdings.sector import backfill_holding_sectors
    _holding(db_session, "AAPL", sector=None)
    with patch("marketpulse.holdings.sector.get_sector", return_value="Tech"):
        n1 = backfill_holding_sectors(db_session)
        n2 = backfill_holding_sectors(db_session)
    assert n1 == 1
    assert n2 == 0
```

- [ ] **Step 2.2: Run tests — should fail with ImportError**

```bash
uv run pytest tests/holdings/test_sector.py -v
```

Expected: All 8 tests fail with `ModuleNotFoundError: No module named 'marketpulse.holdings.sector'`.

- [ ] **Step 2.3: Implement `marketpulse/holdings/sector.py`**

```python
"""yfinance sector lookup with 24h in-memory cache + DB persistence.

Used by Phase 5d /holdings to populate the 板块 (sector) column.
Bounded backfill keeps the /holdings render path under 6s on cold cache
(3 yfinance calls × ~2s each).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Process-level cache: ticker → (sector_or_None, fetched_at)
_cache: dict[str, tuple[str | None, datetime]] = {}
_TTL = timedelta(hours=24)


def get_sector(ticker: str) -> str | None:
    """Lookup sector from yfinance .info['sector'], cached 24h.

    Returns None when fetch fails or sector key is missing.
    Caller decides whether to fall back to '未分类'.
    """
    now = datetime.now(UTC)
    cached = _cache.get(ticker)
    if cached and (now - cached[1]) < _TTL:
        return cached[0]
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        sector = info.get("sector") or None
    except Exception:
        sector = None
    _cache[ticker] = (sector, now)
    return sector


def backfill_holding_sectors(
    session: Session,
    *,
    max_per_call: int = 3,
) -> int:
    """Fill Holding.sector for rows where it's NULL. Bounded + idempotent.

    yfinance .info is ~1-3s per ticker. To avoid blocking the /holdings
    render path for tens of seconds on first load, we cap to `max_per_call`
    per request. Subsequent renders pick up the next batch. After
    ceil(N/max_per_call) page visits all rows are filled.

    Returns count of rows newly filled.
    """
    from marketpulse.db.models import Holding
    holdings = (
        session.query(Holding)
        .filter(Holding.sector.is_(None))
        .limit(max_per_call)
        .all()
    )
    n = 0
    for h in holdings:
        sec = get_sector(h.ticker)
        if sec:
            h.sector = sec
            n += 1
    if n > 0:
        session.commit()
    return n
```

- [ ] **Step 2.4: Run tests — should pass**

```bash
uv run pytest tests/holdings/test_sector.py -v
```

Expected: `8 passed`.

- [ ] **Step 2.5: Ruff clean**

```bash
uv run ruff check marketpulse/holdings/sector.py tests/holdings/test_sector.py
```

Expected: `All checks passed!`

- [ ] **Step 2.6: Commit**

```bash
git add marketpulse/holdings/sector.py tests/holdings/test_sector.py
git commit -m "feat(holdings): sector module — yfinance lookup + bounded backfill

24h process-level cache + DB persistence via Holding.sector.
backfill_holding_sectors capped at max_per_call=3 to avoid blocking
the /holdings render on cold yfinance fetch (~2s per ticker).

8 tests cover: yfinance happy path, fetch failure → None, missing
sector field → None, cache hit, cache expiry after 25h, backfill
NULL-only, max_per_call cap, idempotency."
```

---

### Task 3: Extend `enrich_holdings` — sector / today_change_pct / sparkline

**Files:**
- Modify: `marketpulse/holdings/service.py:24-54` (extend existing function)
- Test: `tests/holdings/test_aggregations.py` (extend)

- [ ] **Step 3.1: Write failing tests**

Append to `tests/holdings/test_aggregations.py`:

```python
def test_enrich_holdings_adds_sector_today_change_sparkline(db_session):
    """enrich_holdings preserves existing fields and adds 3 new ones."""
    from unittest.mock import MagicMock
    from marketpulse.data.types import Bar, Quote
    from marketpulse.db.models import Holding
    from marketpulse.holdings.service import enrich_holdings

    h = Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0, sort_order=0,
                sector="Technology")
    db_session.add(h)
    db_session.commit()

    fake_data = MagicMock()
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=1.5, volume=1000,
        avg_volume_20d=2000, fetched_at=_dt(2026, 5, 15), stale=False,
    )
    fake_data.get_history.return_value = [
        Bar(date=date(2026, 5, d), open=140.0, high=151.0, low=139.0,
            close=140.0 + d, volume=1000)
        for d in range(1, 16)
    ]

    rows = enrich_holdings([h], fake_data)
    assert len(rows) == 1
    r = rows[0]
    # Existing fields preserved
    assert r["ticker"] == "AAPL"
    assert r["market_value"] == pytest.approx(1500.0)
    # New Phase 5d fields
    assert r["sector"] == "Technology"
    assert r["today_change_pct"] == pytest.approx(1.5)
    assert r["sparkline"] == [141.0, 142.0, 143.0, 144.0, 145.0,
                              146.0, 147.0, 148.0, 149.0, 150.0,
                              151.0, 152.0, 153.0, 154.0, 155.0]


def test_enrich_holdings_null_sector_falls_back_unclassified(db_session):
    from unittest.mock import MagicMock
    from marketpulse.data.types import Quote
    from marketpulse.db.models import Holding
    from marketpulse.holdings.service import enrich_holdings

    h = Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0, sort_order=0,
                sector=None)
    db_session.add(h)
    db_session.commit()

    fake_data = MagicMock()
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=0.5, volume=1000,
        avg_volume_20d=2000, fetched_at=_dt(2026, 5, 15), stale=False,
    )
    fake_data.get_history.return_value = []

    rows = enrich_holdings([h], fake_data)
    assert rows[0]["sector"] == "未分类"
    assert rows[0]["sparkline"] == []


def test_enrich_holdings_quote_failure_today_change_none(db_session):
    """Pre-existing tolerance: if quote fetch fails, today_change_pct=None."""
    from unittest.mock import MagicMock
    from marketpulse.db.models import Holding
    from marketpulse.holdings.service import enrich_holdings

    h = Holding(ticker="ZZZ", quantity=10.0, avg_cost=100.0, sort_order=0)
    db_session.add(h)
    db_session.commit()

    fake_data = MagicMock()
    fake_data.get_quote.side_effect = RuntimeError("yfinance down")
    fake_data.get_history.side_effect = RuntimeError("yfinance down")

    rows = enrich_holdings([h], fake_data)
    assert rows[0]["today_change_pct"] is None
    assert rows[0]["sparkline"] == []
```

- [ ] **Step 3.2: Run tests — should fail**

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k enrich_holdings
```

Expected: tests fail because the new fields aren't in the row dict.

- [ ] **Step 3.3: Read current `enrich_holdings` (service.py:24-54) to confirm structure**

```bash
sed -n '24,57p' marketpulse/holdings/service.py
```

You should see: tries `data.get_quote(h.ticker)` in a try/except, builds a row dict with keys `id`, `ticker`, `notes`, `quantity`, `avg_cost`, `current_price`, `market_value`, `cost_basis`, `pl_dollars`, `pl_pct`, `stale`.

- [ ] **Step 3.4: Extend `enrich_holdings`**

Open `marketpulse/holdings/service.py`. Find the row dict construction loop and add the 3 new fields immediately after the existing keys (inside the existing try/except so quote failures are still tolerated). Then add a `_fetch_sparkline` helper at module level.

```python
# Inside the loop, after existing row[...] = ... assignments:
row["sector"] = h.sector or "未分类"
row["today_change_pct"] = quote.change_pct if quote is not None else None
row["sparkline"] = _fetch_sparkline(data, h.ticker)
```

Add at module level (above `enrich_holdings`):

```python
def _fetch_sparkline(data: "_DataLike", ticker: str) -> list[float]:
    """Return last 30 daily closes; [] on fetch failure.

    Used by /holdings table 30-day sparkline column. Failures are
    silenced (yfinance returns 5xx, ticker not found, etc.) so a
    single bad ticker doesn't break the entire table render.
    """
    try:
        bars = data.get_history(ticker, period="30d")
        return [b.close for b in bars[-30:]]
    except Exception:
        return []
```

Extend `_DataLike` protocol (top of file ~line 20) with:

```python
class _DataLike(Protocol):
    def get_quote(self, ticker: str) -> Quote: ...
    def get_history(self, ticker: str, period: str = "30d") -> list[Any]: ...
```

(The exact `Bar` type is fine as `Any` in the Protocol.)

- [ ] **Step 3.5: Run tests — should pass**

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k enrich_holdings
```

Expected: 3 new tests pass. Run full holdings test suite to confirm no regression:

```bash
uv run pytest tests/holdings/ tests/web/test_holdings.py -q
```

Expected: all pass.

- [ ] **Step 3.6: Ruff + commit**

```bash
uv run ruff check marketpulse/holdings/service.py tests/holdings/test_aggregations.py
git add marketpulse/holdings/service.py tests/holdings/test_aggregations.py
git commit -m "feat(holdings): enrich_holdings adds sector/today_change_pct/sparkline

Extends (not replaces) existing function — preserves None-tolerance
for quote failures, stale flag, id/notes fields.

- sector: h.sector or '未分类' fallback
- today_change_pct: quote.change_pct or None on failure
- sparkline: last 30 daily closes via new _fetch_sparkline helper

3 new tests cover: happy path, NULL sector fallback, quote failure
preserves None semantics."
```

---

### Task 4: Service `today_portfolio_change`

**Files:**
- Modify: `marketpulse/holdings/service.py` (append)
- Test: `tests/holdings/test_aggregations.py` (extend)

- [ ] **Step 4.1: Write failing tests**

Append to `tests/holdings/test_aggregations.py`:

```python
def test_today_portfolio_change_up_down_counts():
    from marketpulse.holdings.service import today_portfolio_change
    rows = [
        {"market_value": 1000.0, "today_change_pct": 2.0},
        {"market_value": 500.0, "today_change_pct": -1.0},
        {"market_value": 200.0, "today_change_pct": 0.5},
    ]
    result = today_portfolio_change(rows)
    assert result["up_count"] == 2
    assert result["down_count"] == 1


def test_today_portfolio_change_dollars_sum():
    from marketpulse.holdings.service import today_portfolio_change
    rows = [
        {"market_value": 1000.0, "today_change_pct": 2.0},   # +20
        {"market_value": 500.0, "today_change_pct": -1.0},   # -5
    ]
    result = today_portfolio_change(rows)
    assert result["dollars"] == pytest.approx(15.0)


def test_today_portfolio_change_pct_weighted_by_mv():
    """pct is weighted by market value, not row average."""
    from marketpulse.holdings.service import today_portfolio_change
    rows = [
        {"market_value": 1000.0, "today_change_pct": 1.0},   # +10
        {"market_value": 100.0, "today_change_pct": 10.0},   # +10
    ]
    # Total $20 over $1100 → 1.818%
    result = today_portfolio_change(rows)
    assert result["pct"] == pytest.approx(1.818, rel=1e-2)


def test_today_portfolio_change_excludes_none_pct():
    from marketpulse.holdings.service import today_portfolio_change
    rows = [
        {"market_value": 1000.0, "today_change_pct": None},
        {"market_value": 500.0, "today_change_pct": 2.0},
    ]
    result = today_portfolio_change(rows)
    assert result["up_count"] == 1
    assert result["down_count"] == 0
    assert result["dollars"] == pytest.approx(10.0)


def test_today_portfolio_change_empty_returns_zero():
    from marketpulse.holdings.service import today_portfolio_change
    assert today_portfolio_change([]) == {
        "dollars": 0.0, "pct": 0.0, "up_count": 0, "down_count": 0,
    }


def test_today_portfolio_change_all_none_returns_zero():
    from marketpulse.holdings.service import today_portfolio_change
    rows = [{"market_value": 1000.0, "today_change_pct": None}]
    assert today_portfolio_change(rows) == {
        "dollars": 0.0, "pct": 0.0, "up_count": 0, "down_count": 0,
    }
```

- [ ] **Step 4.2: Run, fail (function doesn't exist)**

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k today_portfolio_change
```

- [ ] **Step 4.3: Implement — append to `marketpulse/holdings/service.py`**

```python
def today_portfolio_change(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate today's portfolio change.

    Rows without today_change_pct (e.g., quote fetch failed) are excluded
    from up/down/dollars but irrelevant for percentage calc since they
    contribute neither numerator nor denominator.

    Returns:
      dollars: sum of (market_value * today_change_pct/100) for eligible rows
      pct: weighted by market_value of eligible rows
      up_count: rows with today_change_pct > 0
      down_count: rows with today_change_pct < 0
    """
    eligible = [r for r in rows if r.get("today_change_pct") is not None]
    if not eligible:
        return {"dollars": 0.0, "pct": 0.0, "up_count": 0, "down_count": 0}

    dollars = sum(r["market_value"] * r["today_change_pct"] / 100 for r in eligible)
    total_mv = sum(r["market_value"] for r in eligible)
    pct = (dollars / total_mv * 100) if total_mv else 0.0
    up_count = sum(1 for r in eligible if r["today_change_pct"] > 0)
    down_count = sum(1 for r in eligible if r["today_change_pct"] < 0)
    return {
        "dollars": dollars,
        "pct": pct,
        "up_count": up_count,
        "down_count": down_count,
    }
```

- [ ] **Step 4.4: Run, pass**

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k today_portfolio_change
```

Expected: 6 pass.

- [ ] **Step 4.5: Ruff + commit**

```bash
uv run ruff check marketpulse/holdings/service.py tests/holdings/test_aggregations.py
git add marketpulse/holdings/service.py tests/holdings/test_aggregations.py
git commit -m "feat(holdings): today_portfolio_change aggregation

Excludes rows with today_change_pct=None (quote fetch failures);
pct is market-value weighted across eligible rows. 6 tests cover
up/down counts, dollars sum, weighted pct, None exclusion, empty input."
```

---

### Task 5: Service `contributors_ranked`

**Files:**
- Modify: `marketpulse/holdings/service.py` (append)
- Test: `tests/holdings/test_aggregations.py` (extend)

- [ ] **Step 5.1: Write failing tests**

```python
def test_contributors_ranked_top_n_slice():
    from marketpulse.holdings.service import contributors_ranked
    rows = [
        {"ticker": "A", "pl_dollars": +1000.0, "market_value": 5000.0},
        {"ticker": "B", "pl_dollars": -2000.0, "market_value": 3000.0},
        {"ticker": "C", "pl_dollars": +500.0, "market_value": 2000.0},
        {"ticker": "D", "pl_dollars": +100.0, "market_value": 1000.0},
        {"ticker": "E", "pl_dollars": -50.0, "market_value": 500.0},
        {"ticker": "F", "pl_dollars": +10.0, "market_value": 100.0},
    ]
    result = contributors_ranked(rows, top_n=3)
    assert len(result) == 3
    # Ordered by |pl_dollars| descending
    assert [r["ticker"] for r in result] == ["B", "A", "C"]


def test_contributors_ranked_default_top_n_5():
    from marketpulse.holdings.service import contributors_ranked
    rows = [{"ticker": str(i), "pl_dollars": float(i),
             "market_value": 100.0} for i in range(10)]
    result = contributors_ranked(rows)
    assert len(result) == 5


def test_contributors_ranked_fewer_than_top_n():
    from marketpulse.holdings.service import contributors_ranked
    rows = [
        {"ticker": "A", "pl_dollars": +1000.0, "market_value": 5000.0},
        {"ticker": "B", "pl_dollars": -500.0, "market_value": 3000.0},
    ]
    result = contributors_ranked(rows, top_n=10)
    assert len(result) == 2


def test_contributors_ranked_empty():
    from marketpulse.holdings.service import contributors_ranked
    assert contributors_ranked([]) == []
```

- [ ] **Step 5.2: Run, fail**

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k contributors_ranked
```

- [ ] **Step 5.3: Implement**

Append to `marketpulse/holdings/service.py`:

```python
def contributors_ranked(
    rows: list[dict[str, Any]],
    *,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Top N rows by |pl_dollars| — the biggest movers in absolute terms.

    NOTE: 'biggest by |pl|' does NOT guarantee a mix of positive and
    negative. If a portfolio has 5 large winners and 1 small loser,
    all 5 returned rows will be winners — that's correct behavior
    (the question is 'who moved the needle most').
    """
    ranked = sort_by_pl_impact(rows)
    return ranked[:top_n]
```

- [ ] **Step 5.4: Run, pass + commit**

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k contributors_ranked
uv run ruff check marketpulse/holdings/service.py tests/holdings/test_aggregations.py
git add marketpulse/holdings/service.py tests/holdings/test_aggregations.py
git commit -m "feat(holdings): contributors_ranked(rows, top_n=5)

Top N by |pl_dollars| — reuses sort_by_pl_impact ordering, slices
to top_n. Does not guarantee pos+neg mix (returns biggest movers
regardless of sign). 4 tests cover slice, default, fewer-than-N,
empty."
```

---

### Task 6: Service `sector_breakdown`

**Files:**
- Modify: `marketpulse/holdings/service.py` (append)
- Test: `tests/holdings/test_aggregations.py` (extend)

- [ ] **Step 6.1: Write failing tests**

```python
def test_sector_breakdown_groups_by_sector():
    from marketpulse.holdings.service import sector_breakdown
    rows = [
        {"sector": "Technology", "market_value": 1000.0},
        {"sector": "Technology", "market_value": 500.0},
        {"sector": "Healthcare", "market_value": 300.0},
    ]
    result = sector_breakdown(rows)
    assert len(result) == 2
    # Sorted by market_value desc
    assert result[0]["sector"] == "Technology"
    assert result[0]["market_value"] == pytest.approx(1500.0)
    assert result[0]["holding_count"] == 2
    assert result[1]["sector"] == "Healthcare"
    assert result[1]["holding_count"] == 1


def test_sector_breakdown_pct_sums_to_100():
    from marketpulse.holdings.service import sector_breakdown
    rows = [
        {"sector": "A", "market_value": 600.0},
        {"sector": "B", "market_value": 400.0},
    ]
    result = sector_breakdown(rows)
    assert sum(r["pct"] for r in result) == pytest.approx(100.0)
    assert result[0]["pct"] == pytest.approx(60.0)
    assert result[1]["pct"] == pytest.approx(40.0)


def test_sector_breakdown_unclassified_separate_bucket():
    from marketpulse.holdings.service import sector_breakdown
    rows = [
        {"sector": "Technology", "market_value": 1000.0},
        {"sector": "未分类", "market_value": 200.0},
    ]
    result = sector_breakdown(rows)
    sectors = [r["sector"] for r in result]
    assert "未分类" in sectors


def test_sector_breakdown_empty():
    from marketpulse.holdings.service import sector_breakdown
    assert sector_breakdown([]) == []
```

- [ ] **Step 6.2: Run, fail**

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k sector_breakdown
```

- [ ] **Step 6.3: Implement**

Append to `marketpulse/holdings/service.py`:

```python
def sector_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group rows by sector.

    Returns: [{sector, market_value, pct, holding_count}, ...]
    sorted by market_value desc. '未分类' falls naturally to its own bucket.
    """
    from collections import defaultdict
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {"market_value": 0.0, "holding_count": 0},
    )
    for r in rows:
        s = r["sector"]
        buckets[s]["market_value"] += r["market_value"]
        buckets[s]["holding_count"] += 1
    total = sum(b["market_value"] for b in buckets.values())
    out = [
        {
            "sector": sector,
            "market_value": v["market_value"],
            "pct": (v["market_value"] / total * 100) if total else 0.0,
            "holding_count": v["holding_count"],
        }
        for sector, v in buckets.items()
    ]
    out.sort(key=lambda x: x["market_value"], reverse=True)
    return out
```

- [ ] **Step 6.4: Run, pass + commit**

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k sector_breakdown
uv run ruff check marketpulse/holdings/service.py tests/holdings/test_aggregations.py
git add marketpulse/holdings/service.py tests/holdings/test_aggregations.py
git commit -m "feat(holdings): sector_breakdown groups by sector

Returns [{sector, market_value, pct, holding_count}] sorted by mv desc.
'未分类' falls into its own bucket naturally. 4 tests cover grouping,
pct sum=100, unclassified, empty."
```

---

### Task 7: GET `/holdings/risk-analysis` (POST→GET refactor)

**Files:**
- Modify: `marketpulse/web/routes/holdings.py` (refactor existing handler at line ~141)
- Create: `marketpulse/web/templates/partials/holdings_risk_card.html`
- Test: `tests/web/test_holdings_risk.py` (new)

- [ ] **Step 7.1: Read current implementation**

```bash
sed -n '141,170p' marketpulse/web/routes/holdings.py
```

Note the current logic: builds portfolio context, calls Anthropic via `ai/service.py`, renders `partials/risk_analysis.html` with `markdown=...` and `error=...`. Your job is to make a GET handler that calls the same AI logic but renders the NEW partial `partials/holdings_risk_card.html` with `analysis_markdown=...`.

- [ ] **Step 7.2: Write failing tests**

Create `tests/web/test_holdings_risk.py`:

```python
from unittest.mock import patch

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_risk_analysis_get_returns_card(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    with patch("marketpulse.ai.service.analyze_portfolio_risk", return_value="## 风险评估\n\n- 项目 1\n- 项目 2"):
        r = client.get("/holdings/risk-analysis")
    assert r.status_code == 200
    assert "mp-card" in r.text
    assert "AI 风险分析" in r.text


def test_risk_analysis_renders_markdown_html(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    with patch("marketpulse.ai.service.analyze_portfolio_risk", return_value="## 标题\n\n- 要点 A"):
        r = client.get("/holdings/risk-analysis")
    # Markdown headers become <h2>
    assert "<h2>" in r.text or "标题" in r.text
    assert "要点 A" in r.text


def test_risk_analysis_handles_anthropic_error_returns_fallback(client: TestClient, monkeypatch):
    """Anthropic raises → 200 OK with fallback card."""
    _login(client, monkeypatch)
    with patch("marketpulse.ai.service.analyze_portfolio_risk", side_effect=RuntimeError("API down")):
        r = client.get("/holdings/risk-analysis")
    assert r.status_code == 200
    assert "AI 服务暂时不可用" in r.text or "稍后重试" in r.text
```

- [ ] **Step 7.3: Run, fail**

```bash
uv run pytest tests/web/test_holdings_risk.py -v
```

Expected: failures (route doesn't exist as GET; new template doesn't exist).

- [ ] **Step 7.4: Create `marketpulse/web/templates/partials/holdings_risk_card.html`**

```html
<section class="mp-card" id="holdings-risk-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">auto_awesome</span>AI 风险分析
    </span>
    <span class="mp-card__sub">{{ generated_at or "刚刚生成" }}</span>
  </div>
  <div class="mp-card__body mp-prose">
    {{ analysis_markdown | markdown }}
  </div>
</section>
```

- [ ] **Step 7.5: Refactor route handler**

In `marketpulse/web/routes/holdings.py`, find the POST `/holdings/risk-analysis` handler (around line 141). Change its decorator and signature to GET, and update the render:

```python
@router.get("/holdings/risk-analysis", response_class=HTMLResponse)
def holdings_risk_analysis(
    request: Request,
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    """HTMX endpoint: AI risk analysis card.

    Called by hx-trigger='load' on the placeholder in /holdings page.
    Always returns 200 — even on Anthropic failure, renders a fallback
    card so HTMX swaps in a sensible state.
    """
    from marketpulse.ai.service import analyze_portfolio_risk

    # Build portfolio context (re-use existing logic from old POST handler)
    holdings = db.query(Holding).order_by(Holding.sort_order, Holding.id).all()
    rows = enrich_holdings(holdings, data)

    try:
        analysis_markdown = analyze_portfolio_risk(rows)
    except Exception:
        analysis_markdown = "**AI 服务暂时不可用,请稍后重试。**"

    return templates.TemplateResponse(
        request,
        "partials/holdings_risk_card.html",
        {"analysis_markdown": analysis_markdown, "generated_at": None},
    )
```

If a POST `/holdings/risk-analysis` was the only caller of the old function, this replaces it cleanly. If other callers exist (search via `git grep "holdings/risk-analysis"`), keep the POST as an alias that dispatches to the same function.

- [ ] **Step 7.6: Run tests + lint + commit**

```bash
uv run pytest tests/web/test_holdings_risk.py -v
uv run ruff check marketpulse/web/routes/holdings.py
git add marketpulse/web/routes/holdings.py \
        marketpulse/web/templates/partials/holdings_risk_card.html \
        tests/web/test_holdings_risk.py
git commit -m "feat(holdings): GET /holdings/risk-analysis HTMX endpoint

POST→GET refactor for hx-trigger='load' on the new holdings page.
Renders partials/holdings_risk_card.html (new) with analysis_markdown
variable; AI errors render a friendly fallback card (HTTP 200, never
4xx, so HTMX swaps cleanly).

3 tests: happy path, markdown HTML rendering, fallback on Anthropic
exception."
```

---

### Task 8: GET `/holdings/export.csv` (streaming)

**Files:**
- Modify: `marketpulse/web/routes/holdings.py` (append handler)
- Test: `tests/web/test_holdings_export.py` (new)

- [ ] **Step 8.1: Write failing tests**

Create `tests/web/test_holdings_export.py`:

```python
import csv
from io import StringIO

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.models import Holding


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_export_csv_content_type(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")


def test_export_csv_filename_header(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings/export.csv")
    cd = r.headers["content-disposition"]
    assert "attachment" in cd
    assert "holdings-" in cd and ".csv" in cd


def test_export_csv_header_row(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings/export.csv")
    first_line = r.text.split("\n")[0]
    assert first_line == "ticker,name,sector,quantity,avg_cost,current_price,market_value,cost_basis,unrealized_pl,unrealized_pl_pct,dividends_received"


def test_export_csv_includes_holding_rows(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0, sector="Technology"))
    db_session.commit()
    r = client.get("/holdings/export.csv")
    rows = list(csv.reader(StringIO(r.text)))
    assert len(rows) >= 2  # header + at least one data row
    data_row = rows[1]
    assert data_row[0] == "AAPL"


def test_export_csv_empty_holdings_header_only(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings/export.csv")
    lines = [ln for ln in r.text.split("\n") if ln.strip()]
    assert len(lines) == 1  # header only
```

- [ ] **Step 8.2: Run, fail (404)**

```bash
uv run pytest tests/web/test_holdings_export.py -v
```

- [ ] **Step 8.3: Implement — append to `marketpulse/web/routes/holdings.py`**

```python
@router.get("/holdings/export.csv")
def holdings_export_csv(
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    """Streaming CSV export of current holdings.

    Format columns:
      ticker, name, sector, quantity, avg_cost, current_price,
      market_value, cost_basis, unrealized_pl, unrealized_pl_pct,
      dividends_received
    """
    from datetime import UTC, datetime
    from fastapi.responses import StreamingResponse

    HEADER = [
        "ticker", "name", "sector", "quantity", "avg_cost",
        "current_price", "market_value", "cost_basis",
        "unrealized_pl", "unrealized_pl_pct", "dividends_received",
    ]

    def _gen():
        yield ",".join(HEADER) + "\n"
        holdings = db.query(Holding).order_by(Holding.sort_order, Holding.id).all()
        if not holdings:
            return
        rows = enrich_holdings(holdings, data)
        divs_by_ticker = per_ticker_dividends(db)
        for r in rows:
            divs = divs_by_ticker.get(r["ticker"], 0.0)
            yield (
                f'{r["ticker"]},{r["ticker"]},{r["sector"]},'
                f'{r["quantity"]:g},{r["avg_cost"]:.4f},'
                f'{r.get("current_price", "") or ""},'
                f'{r.get("market_value", "") or ""},'
                f'{r["cost_basis"]:.2f},'
                f'{(r.get("pl_dollars") or 0):.2f},'
                f'{(r.get("pl_pct") or 0):.4f},'
                f'{divs:.2f}\n'
            )

    filename = f"holdings-{datetime.now(UTC).date().isoformat()}.csv"
    return StreamingResponse(
        _gen(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 8.4: Run tests + lint + commit**

```bash
uv run pytest tests/web/test_holdings_export.py -v
uv run ruff check marketpulse/web/routes/holdings.py tests/web/test_holdings_export.py
git add marketpulse/web/routes/holdings.py tests/web/test_holdings_export.py
git commit -m "feat(holdings): GET /holdings/export.csv streaming export

11 columns: ticker, name (= ticker placeholder, Quote has no name
field), sector, quantity, avg_cost, current_price, market_value,
cost_basis, unrealized_pl, unrealized_pl_pct, dividends_received.
StreamingResponse for memory-friendly export of large portfolios.

5 tests cover content-type, attachment filename, header row, data
rows, empty-holdings (header-only)."
```

---

### Task 9: `/holdings` route — extend context dict with KPI block

**Files:**
- Modify: `marketpulse/web/routes/holdings.py:40-72` (extend `holdings_page`)
- Test: `tests/web/test_holdings.py` (extend)

- [ ] **Step 9.1: Write failing tests**

Append to `tests/web/test_holdings.py` (this verifies new context keys are present in rendered HTML):

```python
def test_holdings_page_renders_with_new_kpi_context(client, monkeypatch):
    """Smoke test: page renders without UndefinedError after route refactor."""
    _login(client, monkeypatch)
    r = client.get("/holdings")
    assert r.status_code == 200
    # YTD KPI label or value should appear
    assert "已实现盈亏" in r.text or "YTD" in r.text
```

(More visual-anchor tests come in template tasks 10+.)

- [ ] **Step 9.2: Run, expect existing tests still pass; new test may pass coincidentally if old template doesn't error out**

```bash
uv run pytest tests/web/test_holdings.py -v
```

- [ ] **Step 9.3: Extend `holdings_page`**

Edit `marketpulse/web/routes/holdings.py`. Find `def holdings_page(` (line ~40). Update to:

```python
@router.get("/holdings", response_class=HTMLResponse)
def holdings_page(
    request: Request,
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    from datetime import date as _date
    from marketpulse.holdings.sector import backfill_holding_sectors
    from marketpulse.holdings.service import (
        contributors_ranked,
        sector_breakdown,
        today_portfolio_change,
    )

    # Backfill any NULL sectors (bounded to 3 tickers per call).
    backfill_holding_sectors(db)

    holdings = db.query(Holding).order_by(Holding.sort_order, Holding.id).all()
    rows = enrich_holdings(holdings, data)
    totals = compute_totals(rows)
    realized = total_realized_pl(db)
    dividends_by_ticker = per_ticker_dividends(db)
    for r in rows:
        r["dividends_received"] = dividends_by_ticker.get(r["ticker"], 0.0)

    # Phase 5d KPI block
    today_year_start = _date(_date.today().year, 1, 1)
    monthly_div_list = monthly_dividends(db)
    this_month_div = monthly_div_list[-1]["amount"] if monthly_div_list else 0.0
    kpi = {
        "today_change": today_portfolio_change(rows),
        "ytd_realized": total_realized_pl(db, from_date=today_year_start),
        "this_month_dividends": this_month_div,
    }

    return templates.TemplateResponse(
        request,
        "holdings.html",
        {
            "rows": rows,
            "ranked_rows": sort_by_pl_impact(rows),
            "totals": totals,
            "realized_pl": realized,
            "total_dividends": total_dividends(db),
            "allocation": allocation_breakdown(rows),
            "monthly_pl": monthly_realized_pl(db),
            "monthly_dividends": monthly_div_list,
            "trade_stats": trading_stats(db),
            # Phase 5d additions
            "kpi": kpi,
            "contributors": contributors_ranked(rows, top_n=5),
            "sectors": sector_breakdown(rows),
        },
    )
```

- [ ] **Step 9.4: Run + verify no regression**

```bash
uv run pytest tests/web/test_holdings.py -v
```

Expected: all existing tests + new smoke test pass. The OLD template still renders even with extra context keys (Jinja silently ignores unused keys).

- [ ] **Step 9.5: Ruff + commit**

```bash
uv run ruff check marketpulse/web/routes/holdings.py
git add marketpulse/web/routes/holdings.py tests/web/test_holdings.py
git commit -m "feat(holdings): route extension — KPI block + contributors + sectors

Calls backfill_holding_sectors (bounded 3/render) → enrich_holdings
(now produces sector/today_change_pct/sparkline per row) → builds
kpi dict (today_change, ytd_realized, this_month_dividends) and
new contributors/sectors aggregations. Old template still renders
correctly since unused context keys are silently ignored."
```

---

### Task 10: Rewrite `holdings.html` shell + `mp-holdings-*` layout CSS

**Files:**
- Rewrite: `marketpulse/web/templates/holdings.html`
- Modify: `marketpulse/web/static/css/app.css` (append)
- Test: `tests/web/test_holdings.py` (extend)

- [ ] **Step 10.1: Write failing tests**

Append to `tests/web/test_holdings.py`:

```python
def test_holdings_page_visual_anchors_present(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings")
    for cls in ("mp-holdings-hero", "mp-holdings-kpi",
                "mp-holdings-row3", "mp-holdings-table",
                "mp-holdings-bottom"):
        assert cls in r.text, f"missing {cls}"


def test_holdings_page_h1_renders(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings")
    assert "Holdings · Portfolio Overview" in r.text


def test_holdings_page_uses_2400_max_width(client, monkeypatch):
    """Like /stock and /trades, /holdings must override base.html's
    default max-w-5xl with max-w-[2400px]."""
    _login(client, monkeypatch)
    r = client.get("/holdings")
    assert "max-w-[2400px]" in r.text
```

- [ ] **Step 10.2: Run, fail**

```bash
uv run pytest tests/web/test_holdings.py -v -k "visual_anchors or h1_renders or 2400"
```

- [ ] **Step 10.3: Rewrite `marketpulse/web/templates/holdings.html`**

Replace entire file with:

```html
{% extends "base.html" %}
{% block main_width %}max-w-[2400px]{% endblock %}
{% block nav_width %}max-w-[2400px]{% endblock %}
{% block content %}

{% include "partials/holdings_hero.html" ignore missing %}

<section class="mp-holdings-kpi">
  {% include "partials/holdings_kpi_strip.html" ignore missing %}
</section>

<section class="mp-holdings-row3">
  {% include "partials/holdings_allocation_card.html" ignore missing %}
  {% include "partials/holdings_sector_card.html" ignore missing %}
  {% include "partials/holdings_contributors_card.html" ignore missing %}
</section>

<section class="mp-holdings-table">
  <div id="holdings-container">
    {% include "partials/holdings_table.html" %}
  </div>
</section>

<section class="mp-holdings-bottom">
  {% include "partials/holdings_monthly_card.html" ignore missing %}
  <div id="holdings-risk-card"
       hx-get="/holdings/risk-analysis"
       hx-trigger="load"
       hx-swap="outerHTML">
    <section class="mp-card">
      <div class="mp-card__head">
        <span class="mp-card__title">
          <span class="material-symbols-outlined">auto_awesome</span>AI 风险分析
        </span>
      </div>
      <div class="mp-card__body mp-risk-loading">
        <span class="muted">正在分析…</span>
      </div>
    </section>
  </div>
</section>

{% endblock %}
```

`ignore missing` lets later tasks fill in the partials incrementally without breaking page render.

- [ ] **Step 10.4: Note about `holdings_table.html`**

The existing `partials/holdings_table.html` will be rewritten in Task 16. Until then, the old (Tailwind) table renders. That's acceptable — the page won't crash because all rows/totals context keys are still passed.

- [ ] **Step 10.5: Append CSS to `marketpulse/web/static/css/app.css`**

```css
/* ════════ Phase 5d: /holdings layout ════════ */
.mp-holdings-hero        { padding:32px 48px 24px;
                           display:grid; grid-template-columns:1fr 360px; gap:48px;
                           align-items:flex-start; }
.mp-holdings-hero__title { font:700 48px/1 var(--ns-font-headline);
                           letter-spacing:-0.04em; color:var(--ns-navy); margin:6px 0 0; }
.mp-holdings-hero__stats { display:flex; align-items:flex-end; gap:48px; margin-top:28px; }
.mp-holdings-hero__mv-value { font:600 60px/1 var(--ns-font-mono); letter-spacing:-0.04em;
                              color:var(--ns-navy); }
.mp-holdings-hero__pl-value { font:700 32px/1.05 var(--ns-font-headline);
                              letter-spacing:-0.02em; }

.mp-holdings-kpi         { padding:0 48px 16px;
                           display:grid; grid-template-columns:repeat(5,1fr); gap:16px; }
.mp-holdings-row3        { padding:16px 48px 16px;
                           display:grid; grid-template-columns:1.4fr 1fr 1.4fr; gap:16px; }
.mp-holdings-table       { padding:0 48px 16px; }
.mp-holdings-bottom      { padding:0 48px 32px;
                           display:grid; grid-template-columns:1fr 1fr; gap:16px; }

@media (max-width: 1600px) {
  .mp-holdings-row3      { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 1440px) {
  .mp-holdings-hero      { grid-template-columns: 1fr; }
  .mp-holdings-hero__donut { max-width: 360px; }
  .mp-holdings-row3      { grid-template-columns: 1fr; }
  .mp-holdings-bottom    { grid-template-columns: 1fr; }
}
@media (max-width: 900px) {
  .mp-holdings-kpi       { grid-template-columns: repeat(2, 1fr); }
}

/* Risk card loading state */
.mp-risk-loading         { min-height:200px; display:flex;
                           align-items:center; justify-content:center; }
.mp-prose                { font-size:14px; line-height:1.7;
                           color:var(--ns-on-surface); padding:20px; }
.mp-prose h2, .mp-prose h3 { font-family:var(--ns-font-headline);
                              color:var(--ns-navy); margin-top:16px; }
.mp-prose p              { margin:8px 0; }
.mp-prose ul             { padding-left:24px; }
```

- [ ] **Step 10.6: Run tests + commit**

```bash
uv run pytest tests/web/test_holdings.py -v
git add marketpulse/web/templates/holdings.html marketpulse/web/static/css/app.css tests/web/test_holdings.py
git commit -m "feat(holdings): new holdings.html shell + mp-holdings-* CSS

NineScrolls Variant A 5-section layout:
hero / kpi / 3-card row / 14-col table / bottom (monthly + AI).
Inner partials use ignore-missing so later tasks fill incrementally.
AI risk card uses hx-trigger=load placeholder.
Responsive: < 1600 → 3-card row collapses to 2; < 1440 → full stack;
< 900 → KPI 2-col."
```

---

### Task 11: Hero partial + Donut partial + hero CSS

**Files:**
- Create: `marketpulse/web/templates/partials/holdings_hero.html`
- Create: `marketpulse/web/templates/partials/holdings_donut.html`
- Modify: `marketpulse/web/static/css/app.css` (append donut CSS)
- Test: `tests/web/test_holdings.py` (extend)

- [ ] **Step 11.1: Write failing tests**

```python
def test_holdings_hero_renders_three_big_numbers(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings")
    assert "总市值" in r.text
    assert "未实现盈亏" in r.text
    # 今日 may not always show (empty portfolio) but label appears
    assert "今日" in r.text


def test_holdings_donut_renders_svg(client, monkeypatch, db_session):
    """With at least one holding, donut <svg> should render."""
    _login(client, monkeypatch)
    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0))
    db_session.commit()
    r = client.get("/holdings")
    assert "<svg" in r.text
    assert "viewBox=\"0 0 100 100\"" in r.text
```

- [ ] **Step 11.2: Run, fail**

- [ ] **Step 11.3: Create `partials/holdings_hero.html`**

```html
<section class="mp-holdings-hero">
  <div class="mp-holdings-hero__main">
    <span class="mp-eyebrow mp-eyebrow--primary">投资组合</span>
    <h1 class="grotesk mp-holdings-hero__title">Holdings · Portfolio Overview</h1>
    <span class="mp-rule"></span>
    <div class="mp-holdings-hero__stats">
      <div>
        <span class="mp-eyebrow">总市值 · USD</span>
        <div class="mp-holdings-hero__mv-value mono tnum">
          ${{ "{:,.0f}".format(totals.market_value or 0) }}
        </div>
      </div>
      <div>
        <span class="mp-eyebrow">未实现盈亏</span>
        {% set pl = (totals.market_value or 0) - (totals.cost or 0) %}
        {% set pl_pct = (pl / totals.cost * 100) if totals.cost else 0 %}
        <div class="mp-holdings-hero__pl-value grotesk tnum {% if pl >= 0 %}up{% else %}down{% endif %}">
          {{ "{:+,.0f}".format(pl) }}
        </div>
        <div class="mono tnum {% if pl >= 0 %}up{% else %}down{% endif %}"
             style="font-size:14px; font-weight:600; margin-top:2px;">
          {{ "{:+.2f}%".format(pl_pct) }}
        </div>
      </div>
      <div>
        <span class="mp-eyebrow">今日</span>
        {% set tc = kpi.today_change %}
        <div class="grotesk tnum {% if tc.dollars >= 0 %}up{% else %}down{% endif %}"
             style="font-size:32px; font-weight:700; letter-spacing:-0.02em; line-height:1.05;">
          {{ "{:+,.0f}".format(tc.dollars) }}
        </div>
        <div class="mono tnum {% if tc.dollars >= 0 %}up{% else %}down{% endif %}"
             style="font-size:14px; font-weight:600; margin-top:2px;">
          {{ "{:+.2f}%".format(tc.pct) }} · {{ tc.up_count }} 涨 {{ tc.down_count }} 跌
        </div>
      </div>
    </div>
  </div>
  <aside class="mp-holdings-hero__donut">
    {% include "partials/holdings_donut.html" %}
  </aside>
</section>
```

- [ ] **Step 11.4: Create `partials/holdings_donut.html`**

```html
{% set total = allocation | sum(attribute='market_value') %}
{% set palette = ['#0066cc', '#022448', '#0e8a5f', '#c0392b', '#9b59b6', '#16a085', '#c0570c', '#4d94ff'] %}
<div class="mp-donut">
  <svg viewBox="0 0 100 100" width="160" height="160">
    {% set ns = namespace(offset=0) %}
    {% for slice in allocation[:8] %}
      {% set pct = (slice.market_value / total * 100) if total else 0 %}
      {% set dasharray = pct * 2.513 %}
      <circle cx="50" cy="50" r="40" fill="none"
              stroke="{{ palette[loop.index0 % palette|length] }}" stroke-width="14"
              stroke-dasharray="{{ "%.2f"|format(dasharray) }} 251.3"
              stroke-dashoffset="{{ "%.2f"|format(-ns.offset * 2.513) }}"
              transform="rotate(-90 50 50)" />
      {% set ns.offset = ns.offset + pct %}
    {% endfor %}
  </svg>
  <div class="mp-donut__legend">
    <span class="mp-eyebrow mp-eyebrow--primary">主要构成</span>
    {% for slice in allocation[:5] %}
      <div class="mp-donut__legend-row">
        <span class="mp-donut__legend-swatch" style="background:{{ palette[loop.index0 % palette|length] }};"></span>
        <span class="grotesk" style="font-weight:700; font-size:12px; color:var(--ns-navy); flex:1;">{{ slice.ticker }}</span>
        <span class="mono tnum muted" style="font-size:12px;">{{ "%.1f%%"|format((slice.market_value / total * 100) if total else 0) }}</span>
      </div>
    {% endfor %}
  </div>
</div>
```

- [ ] **Step 11.5: Append donut CSS to `app.css`**

```css
/* ════════ Phase 5d: Donut ════════ */
.mp-donut                { display:flex; align-items:center; gap:20px; }
.mp-donut__legend        { display:flex; flex-direction:column; gap:6px; flex:1; }
.mp-donut__legend-row    { display:flex; align-items:center; gap:8px; font-size:12px; }
.mp-donut__legend-swatch { width:10px; height:10px; border-radius:2px; }
```

- [ ] **Step 11.6: Run tests + commit**

```bash
uv run pytest tests/web/test_holdings.py -v
git add marketpulse/web/templates/partials/holdings_hero.html \
        marketpulse/web/templates/partials/holdings_donut.html \
        marketpulse/web/static/css/app.css tests/web/test_holdings.py
git commit -m "feat(holdings): hero partial + donut SVG + donut CSS

Hero (1fr 360px grid): editorial h1 + 3 big numbers (总市值 60px mono,
未P&L 32px navy/up/down, 今日 32px navy/up/down with 涨/跌 count).
Donut: pure SVG with stroke-dasharray arcs, 8-color palette, top-5
legend rows alongside."
```

---

### Task 12: KPI strip partial

**Files:**
- Create: `marketpulse/web/templates/partials/holdings_kpi_strip.html`
- Test: `tests/web/test_holdings.py` (extend)

- [ ] **Step 12.1: Write failing test**

```python
def test_holdings_kpi_strip_5_cards(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings")
    assert r.text.count("mp-kpi__value") == 5
    # All 5 labels appear
    for label in ("总成本", "市值", "未实现盈亏", "已实现盈亏", "累计分红"):
        assert label in r.text
```

- [ ] **Step 12.2: Run, fail**

- [ ] **Step 12.3: Create `partials/holdings_kpi_strip.html`**

```html
{% set pl = (totals.market_value or 0) - (totals.cost or 0) %}
{% set pl_pct = (pl / totals.cost * 100) if totals.cost else 0 %}

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">总成本 · 含手续费</span>
    <span class="material-symbols-outlined mp-kpi__icon">payments</span>
  </div>
  <div class="mp-kpi__value grotesk tnum">${{ "{:,.0f}".format(totals.cost or 0) }}</div>
  <div class="mp-kpi__hint">{{ trade_stats.total_trades }} 笔交易累计</div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">市值</span>
    <span class="material-symbols-outlined mp-kpi__icon">account_balance_wallet</span>
  </div>
  <div class="mp-kpi__value grotesk tnum">${{ "{:,.0f}".format(totals.market_value or 0) }}</div>
  <div class="mp-kpi__hint">实时</div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">未实现盈亏</span>
    <span class="material-symbols-outlined mp-kpi__icon">trending_up</span>
  </div>
  <div class="mp-kpi__value grotesk tnum"
       style="color: {% if pl >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};">
    {{ "{:+,.0f}".format(pl) }}
  </div>
  <div class="mp-kpi__hint">{{ "{:+.2f}%".format(pl_pct) }} · 持仓盈亏</div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">已实现盈亏 · YTD</span>
    <span class="material-symbols-outlined mp-kpi__icon">payments</span>
  </div>
  <div class="mp-kpi__value grotesk tnum"
       style="color: {% if kpi.ytd_realized >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};">
    {{ "{:+,.0f}".format(kpi.ytd_realized) }}
  </div>
  <div class="mp-kpi__hint">
    {% if trade_stats.win_rate_pct is not none %}
      胜率 {{ "%.1f%%"|format(trade_stats.win_rate_pct) }}
    {% else %}
      胜率 —
    {% endif %}
  </div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">累计分红</span>
    <span class="material-symbols-outlined mp-kpi__icon">redeem</span>
  </div>
  <div class="mp-kpi__value grotesk tnum" style="color: var(--mp-up);">
    +${{ "{:,.2f}".format(total_dividends) }}
  </div>
  <div class="mp-kpi__hint">含本月 ${{ "{:.2f}".format(kpi.this_month_dividends) }}</div>
</div>
```

- [ ] **Step 12.4: Run tests + commit**

```bash
uv run pytest tests/web/test_holdings.py -v -k kpi
git add marketpulse/web/templates/partials/holdings_kpi_strip.html tests/web/test_holdings.py
git commit -m "feat(holdings): KPI strip partial — 5 cards

总成本 / 市值 / 未实现P&L (color-coded) / 已实现P&L YTD (color-coded) /
累计分红. Reuses mp-kpi from Phase 5c. win_rate_pct=None handled.
this_month_dividends from kpi context dict."
```

---

### Task 13: Allocation card partial + allocation list CSS

**Files:**
- Create: `marketpulse/web/templates/partials/holdings_allocation_card.html`
- Modify: `marketpulse/web/static/css/app.css` (append)
- Test: `tests/web/test_holdings.py` (extend)

- [ ] **Step 13.1: Write failing test**

```python
def test_holdings_allocation_card_renders(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0))
    db_session.commit()
    r = client.get("/holdings")
    assert "持仓分布 · 按代码" in r.text
    assert "mp-allocation-row" in r.text
```

- [ ] **Step 13.2: Create `partials/holdings_allocation_card.html`**

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">donut_small</span>持仓分布 · 按代码
    </span>
  </div>
  <ul class="mp-allocation-list">
    {% set max_val = allocation | map(attribute='market_value') | max if allocation else 0 %}
    {% for r in allocation %}
      <li class="mp-allocation-row">
        <span class="grotesk" style="font-weight:700; font-size:13px; color:var(--ns-navy); width:60px;">{{ r.ticker }}</span>
        <div class="mp-allocation-bar">
          <div style="width: {{ (r.market_value / max_val * 100) if max_val else 0 }}%; background:var(--ns-navy);"></div>
        </div>
        <span class="mono tnum muted" style="font-size:11.5px; margin-left:auto;">
          {{ "%.1f%%"|format(r.pct) }} · ${{ "{:,.0f}".format(r.market_value) }}
        </span>
      </li>
    {% endfor %}
    {% if not allocation %}
      <li class="muted" style="padding: 16px;">暂无持仓数据</li>
    {% endif %}
  </ul>
</section>
```

- [ ] **Step 13.3: Append CSS**

```css
/* ════════ Phase 5d: Allocation / sector / contributor lists ════════ */
.mp-allocation-list      { list-style:none; margin:0; padding:10px 16px 18px; }
.mp-allocation-row       { display:flex; align-items:center; gap:10px; padding:7px 0; }
.mp-allocation-bar       { flex:1; height:8px; background:var(--ns-surface-container);
                           border-radius:2px; position:relative; overflow:hidden; }
.mp-allocation-bar > div { position:absolute; left:0; top:0; bottom:0; }
```

- [ ] **Step 13.4: Run tests + commit**

```bash
uv run pytest tests/web/test_holdings.py -v -k allocation
git add marketpulse/web/templates/partials/holdings_allocation_card.html \
        marketpulse/web/static/css/app.css tests/web/test_holdings.py
git commit -m "feat(holdings): allocation card partial + mp-allocation-* CSS

By-ticker market_value progress bars, sorted desc (from
allocation_breakdown). Empty state shows '暂无持仓数据'."
```

---

### Task 14: Sector card partial

**Files:**
- Create: `marketpulse/web/templates/partials/holdings_sector_card.html`
- Test: `tests/web/test_holdings.py` (extend)

- [ ] **Step 14.1: Write failing test**

```python
def test_holdings_sector_card_renders(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0, sector="Technology"))
    db_session.commit()
    r = client.get("/holdings")
    assert "板块分布" in r.text


def test_holdings_sector_card_shows_unclassified(client, monkeypatch, db_session):
    """Holdings with NULL sector show under 未分类."""
    _login(client, monkeypatch)
    db_session.add(Holding(ticker="XYZ", quantity=1.0, avg_cost=100.0,
                           sort_order=0, sector=None))
    db_session.commit()
    r = client.get("/holdings")
    assert "未分类" in r.text
```

- [ ] **Step 14.2: Create `partials/holdings_sector_card.html`**

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">category</span>板块分布
    </span>
  </div>
  <ul class="mp-allocation-list">
    {% set max_val = sectors | map(attribute='market_value') | max if sectors else 0 %}
    {% for s in sectors %}
      <li class="mp-allocation-row">
        <span class="grotesk" style="font-weight:700; font-size:12px; color:var(--ns-navy); flex:0 0 110px;">{{ s.sector }}</span>
        <div class="mp-allocation-bar">
          <div style="width: {{ (s.market_value / max_val * 100) if max_val else 0 }}%; background:var(--ns-primary);"></div>
        </div>
        <span class="mono tnum muted" style="font-size:11.5px; margin-left:auto; flex:0 0 80px; text-align:right;">
          {{ "%.1f%%"|format(s.pct) }}
        </span>
      </li>
    {% endfor %}
    {% if not sectors %}
      <li class="muted" style="padding: 16px;">暂无板块数据</li>
    {% endif %}
  </ul>
</section>
```

- [ ] **Step 14.3: Run tests + commit**

```bash
uv run pytest tests/web/test_holdings.py -v -k sector
git add marketpulse/web/templates/partials/holdings_sector_card.html tests/web/test_holdings.py
git commit -m "feat(holdings): sector card partial

Reuses mp-allocation-* CSS; primary-blue bar (vs navy for ticker
allocation). Empty/unclassified states handled."
```

---

### Task 15: Contributors card partial

**Files:**
- Create: `marketpulse/web/templates/partials/holdings_contributors_card.html`
- Test: `tests/web/test_holdings.py` (extend)

- [ ] **Step 15.1: Write failing test**

```python
def test_holdings_contributors_card_renders(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0))
    db_session.commit()
    r = client.get("/holdings")
    assert "盈亏贡献" in r.text or "贡献排行" in r.text
```

- [ ] **Step 15.2: Create `partials/holdings_contributors_card.html`**

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">leaderboard</span>盈亏贡献排行
    </span>
    <span class="mp-card__sub">按 |P&L| 降序</span>
  </div>
  <ul class="mp-allocation-list">
    {% set max_abs = contributors | map(attribute='pl_dollars') | map('abs') | max if contributors else 0 %}
    {% for c in contributors %}
      <li class="mp-allocation-row">
        <span class="grotesk" style="font-weight:700; font-size:13px; color:var(--ns-navy); width:60px;">{{ c.ticker }}</span>
        <div class="mp-allocation-bar">
          <div style="width: {{ (c.pl_dollars|abs / max_abs * 100) if max_abs else 0 }}%;
                      background: {% if c.pl_dollars >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};"></div>
        </div>
        <span class="mono tnum {% if c.pl_dollars >= 0 %}up{% else %}down{% endif %}"
              style="font-size:12px; font-weight:600; margin-left:auto; flex:0 0 90px; text-align:right;">
          {{ "{:+,.0f}".format(c.pl_dollars) }}
        </span>
      </li>
    {% endfor %}
    {% if not contributors %}
      <li class="muted" style="padding: 16px;">暂无盈亏数据</li>
    {% endif %}
  </ul>
</section>
```

- [ ] **Step 15.3: Run tests + commit**

```bash
uv run pytest tests/web/test_holdings.py -v -k contributors
git add marketpulse/web/templates/partials/holdings_contributors_card.html tests/web/test_holdings.py
git commit -m "feat(holdings): contributors card partial

Top-5 by |pl_dollars|, bar colored green/red based on sign,
right-aligned signed dollar value."
```

---

### Task 16: Rewrite `holdings_table.html` (14 cols) + table CSS

**Files:**
- Rewrite: `marketpulse/web/templates/partials/holdings_table.html`
- Modify: `marketpulse/web/static/css/app.css` (append)
- Test: `tests/web/test_holdings.py` (extend)

- [ ] **Step 16.1: Write failing tests**

```python
def test_holdings_table_14_columns(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0))
    db_session.commit()
    r = client.get("/holdings")
    # 14 <th> in header (no <tfoot> th)
    th_count = r.text.count("<th")
    assert th_count >= 14, f"expected >= 14 <th>, got {th_count}"


def test_holdings_table_sparkline_per_row(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0))
    db_session.commit()
    r = client.get("/holdings")
    # Each holding row gets a sparkline if data exists; otherwise '—'.
    # With mocked data we may have either; check for col-spark class.
    assert "col-spark" in r.text


def test_holdings_table_delete_uses_int_id(client, monkeypatch, db_session):
    """DELETE URL must use r.id (int), not r.ticker (string)."""
    _login(client, monkeypatch)
    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0))
    db_session.commit()
    r = client.get("/holdings")
    # Should NOT have hx-delete pointing to /holdings/AAPL
    assert "/holdings/AAPL\"" not in r.text
    # SHOULD have /holdings/<digits>
    import re
    assert re.search(r'hx-delete="/holdings/\d+"', r.text)


def test_holdings_table_tfoot_totals(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    db_session.add(Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0,
                           sort_order=0))
    db_session.commit()
    r = client.get("/holdings")
    assert "<tfoot>" in r.text
    assert "合计" in r.text
```

- [ ] **Step 16.2: Run, fail**

- [ ] **Step 16.3: Rewrite `marketpulse/web/templates/partials/holdings_table.html`**

```html
<table class="mp-table mp-table--holdings">
  <thead>
    <tr>
      <th class="col-ticker">代码</th>
      <th class="col-name">名称</th>
      <th class="col-sector">板块</th>
      <th class="col-qty">数量</th>
      <th class="col-avg">均价</th>
      <th class="col-price">现价</th>
      <th class="col-today">今日%</th>
      <th class="col-cost">总成本</th>
      <th class="col-mv">市值</th>
      <th class="col-pl">未实现盈亏</th>
      <th class="col-plpct">盈亏 %</th>
      <th class="col-spark">30日</th>
      <th class="col-alloc">占组合</th>
      <th class="col-actions"></th>
    </tr>
  </thead>
  <tbody>
    {% for r in rows %}
      <tr id="holding-row-{{ r.ticker }}">
        <td class="col-ticker"><a href="/stock/{{ r.ticker }}" class="mp-ticker-link">{{ r.ticker }}</a></td>
        <td class="col-name muted">{{ r.ticker }}</td>
        <td class="col-sector"><span class="mp-chip">{{ r.sector }}</span></td>
        <td class="col-qty mono tnum">{{ "%g"|format(r.quantity) }}</td>
        <td class="col-avg mono tnum">${{ "%.2f"|format(r.avg_cost) }}</td>
        <td class="col-price mono tnum">
          {% if r.current_price is not none %}${{ "%.2f"|format(r.current_price) }}{% else %}—{% endif %}
        </td>
        <td class="col-today mono tnum {% if r.today_change_pct is not none and r.today_change_pct >= 0 %}up{% elif r.today_change_pct is not none %}down{% endif %}">
          {% if r.today_change_pct is not none %}{{ "{:+.2f}%".format(r.today_change_pct) }}{% else %}—{% endif %}
        </td>
        <td class="col-cost mono tnum">${{ "{:,.2f}".format(r.cost_basis) }}</td>
        <td class="col-mv mono tnum">
          {% if r.market_value is not none %}${{ "{:,.2f}".format(r.market_value) }}{% else %}—{% endif %}
        </td>
        <td class="col-pl mono tnum {% if r.pl_dollars is not none and r.pl_dollars >= 0 %}up{% elif r.pl_dollars is not none %}down{% endif %}">
          {% if r.pl_dollars is not none %}{{ "{:+,.2f}".format(r.pl_dollars) }}{% else %}—{% endif %}
        </td>
        <td class="col-plpct mono tnum {% if r.pl_dollars is not none and r.pl_dollars >= 0 %}up{% elif r.pl_dollars is not none %}down{% endif %}">
          {% if r.pl_pct is not none %}{{ "{:+.2f}%".format(r.pl_pct) }}{% else %}—{% endif %}
        </td>
        <td class="col-spark">
          {% if r.sparkline and r.sparkline|length >= 2 %}
            <svg class="mp-holdings__spark" width="64" height="22" viewBox="0 0 64 22" preserveAspectRatio="none">
              <polyline points="{{ r.sparkline | sparkpoints(64, 22) }}"
                        fill="none"
                        stroke="{% if r.pl_dollars is not none and r.pl_dollars >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %}"
                        stroke-width="1.5" />
            </svg>
          {% else %}<span class="muted">—</span>{% endif %}
        </td>
        <td class="col-alloc">
          {% set allo_pct = (r.market_value / totals.market_value * 100) if (r.market_value and totals.market_value) else 0 %}
          <div class="mp-holdings__allo-bar">
            <div style="width: {{ allo_pct }}%; background: var(--ns-navy);"></div>
          </div>
          <span class="mono tnum muted" style="font-size:11px; margin-left:8px;">{{ "%.1f%%"|format(allo_pct) }}</span>
        </td>
        <td class="col-actions">
          <button class="mp-icon-btn"
                  hx-delete="/holdings/{{ r.id }}"
                  hx-target="#holding-row-{{ r.ticker }}"
                  hx-swap="outerHTML"
                  hx-confirm="删除 {{ r.ticker }} 的所有交易和持仓?">
            <span class="material-symbols-outlined">delete_outline</span>
          </button>
        </td>
      </tr>
    {% endfor %}
    {% if not rows %}
      <tr><td colspan="14" class="mp-empty-row">暂无持仓。先在 <a href="/trades">/trades</a> 添加交易。</td></tr>
    {% endif %}
  </tbody>
  {% if rows %}
  <tfoot>
    <tr class="mp-table__totals">
      <td colspan="7"><span class="grotesk" style="font-weight:700; font-size:12px; letter-spacing:0.04em; color:var(--ns-navy);">合计 · {{ rows|length }} 个标的</span></td>
      <td class="mono tnum" style="font-weight:700; color:var(--ns-navy);">${{ "{:,.0f}".format(totals.cost or 0) }}</td>
      <td class="mono tnum" style="font-weight:700; color:var(--ns-navy);">${{ "{:,.0f}".format(totals.market_value or 0) }}</td>
      {% set pl = (totals.market_value or 0) - (totals.cost or 0) %}
      {% set pl_pct = (pl / totals.cost * 100) if totals.cost else 0 %}
      <td class="mono tnum {% if pl >= 0 %}up{% else %}down{% endif %}" style="font-weight:700;">{{ "{:+,.0f}".format(pl) }}</td>
      <td class="mono tnum {% if pl >= 0 %}up{% else %}down{% endif %}" style="font-weight:700;">{{ "{:+.2f}%".format(pl_pct) }}</td>
      <td colspan="3"></td>
    </tr>
  </tfoot>
  {% endif %}
</table>
```

- [ ] **Step 16.4: Append table CSS to `app.css`**

```css
/* ════════ Phase 5d: Holdings table ════════ */
#holdings-container      { overflow-x: auto; }
.mp-table--holdings      { min-width: 1400px; width:100%; border-collapse:collapse; }
.mp-table--holdings th   { font:600 10px/1 var(--ns-font-headline);
                           letter-spacing:0.08em; text-transform:uppercase;
                           color:var(--ns-on-surface-variant); padding:10px 12px;
                           border-bottom:1px solid var(--ns-outline-variant);
                           white-space:nowrap; text-align:left; }
.mp-table--holdings td   { padding:12px; font-size:13px;
                           border-bottom:1px solid var(--ns-outline-variant);
                           vertical-align:middle; }
.mp-table--holdings tbody tr:hover { background: var(--ns-surface-container-low); }
.mp-table--holdings .col-qty,
.mp-table--holdings .col-avg,
.mp-table--holdings .col-price,
.mp-table--holdings .col-today,
.mp-table--holdings .col-cost,
.mp-table--holdings .col-mv,
.mp-table--holdings .col-pl,
.mp-table--holdings .col-plpct { text-align:right; }
.mp-table--holdings .up   { color:var(--mp-up); font-weight:600; }
.mp-table--holdings .down { color:var(--mp-down); font-weight:600; }
.mp-table--holdings .muted{ color:var(--ns-on-surface-variant); }
.mp-table--holdings tfoot td { background:var(--ns-surface-container-low);
                               border-top:2px solid var(--ns-outline-variant); }

.mp-holdings__spark      { display:block; }
.mp-holdings__allo-bar   { display:inline-block; width:80px; height:8px;
                           background:var(--ns-surface-container); border-radius:2px;
                           position:relative; overflow:hidden; vertical-align:middle; }
.mp-holdings__allo-bar > div { position:absolute; left:0; top:0; bottom:0; }
```

- [ ] **Step 16.5: Run tests + commit**

```bash
uv run pytest tests/web/test_holdings.py -v
git add marketpulse/web/templates/partials/holdings_table.html \
        marketpulse/web/static/css/app.css tests/web/test_holdings.py
git commit -m "feat(holdings): 14-col holdings table rewrite + table CSS

代码/名称/板块/数量/均价/现价/今日%/总成本/市值/未P&L/盈亏%/30日/占组合/actions.
DELETE uses int r.id (not r.ticker string). Sparkline via sparkpoints
filter (5c). Allocation bar inline. tfoot 合计 row. None-tolerance
throughout for failed quotes. overflow-x:auto + min-width:1400px
for < 1400 horizontal scroll."
```

---

### Task 17: Monthly card partial (reuse 5c bar chart style)

**Files:**
- Create: `marketpulse/web/templates/partials/holdings_monthly_card.html`
- Test: `tests/web/test_holdings.py` (extend)

- [ ] **Step 17.1: Write failing test**

```python
def test_holdings_monthly_card_renders(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/holdings")
    assert "月度已实现盈亏" in r.text
    # mp-monthly-bars class from Phase 5c
    assert "mp-monthly-bars" in r.text
```

- [ ] **Step 17.2: Create `partials/holdings_monthly_card.html`**

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">insights</span>月度已实现盈亏
    </span>
    {% set monthly_total = monthly_pl | sum(attribute='pl') %}
    <span class="mp-card__sub">
      {{ monthly_pl|length }} 个月 · 累计
      <span class="{% if monthly_total >= 0 %}up{% else %}down{% endif %} mono"
            style="font-weight:700;">{{ "{:+,.0f}".format(monthly_total) }}</span>
    </span>
  </div>
  <div class="mp-card__body">
    {% if monthly_pl %}
      {% set max_abs_list = monthly_pl | map(attribute='pl') | map('abs') | list %}
      {% set max_v = max_abs_list | max if max_abs_list else 0 %}
      <div class="mp-monthly-bars">
        {% for m in monthly_pl %}
          {% set pct = (m.pl|abs / max_v * 100) if max_v else 0 %}
          <div class="mp-monthly-bar" title="{{ m.month }}: {{ '{:+,.0f}'.format(m.pl) }}">
            <div class="mp-monthly-bar__bar"
                 style="height: {{ pct }}%;
                        background: {% if m.pl >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};"></div>
            <div class="mp-monthly-bar__label">{{ m.month[5:] }}</div>
          </div>
        {% endfor %}
      </div>
    {% else %}
      <div class="muted" style="padding:32px; text-align:center;">暂无已实现盈亏数据</div>
    {% endif %}
  </div>
</section>
```

- [ ] **Step 17.3: Run tests + commit**

```bash
uv run pytest tests/web/test_holdings.py -v -k monthly
git add marketpulse/web/templates/partials/holdings_monthly_card.html tests/web/test_holdings.py
git commit -m "feat(holdings): monthly P&L card partial

Reuses mp-monthly-bars CSS from Phase 5c. Uses monthly_pl context
(no months= arg, so it's all-time per the existing /holdings
behavior). Empty state handled."
```

---

### Task 18: Final integration — full suite + ruff + commit log

**Files:**
- All previous task outputs

- [ ] **Step 18.1: Run full test suite**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: all tests pass, 0 failures. Total count should be `(429 + ~40 new) ≈ 469+`.

- [ ] **Step 18.2: Ruff on entire repo**

```bash
uv run ruff check . 2>&1 | tail -3
```

Expected: `All checks passed!`

- [ ] **Step 18.3: Verify migration applied + reversible**

```bash
uv run alembic current
uv run alembic downgrade -1
uv run alembic upgrade head
```

Each command exits 0, no errors.

- [ ] **Step 18.4: Smoke test the route**

```bash
uv run python -c "
from fastapi.testclient import TestClient
from marketpulse.web.main import app
import os
os.environ['APP_PASSWORD_HASH'] = '\$argon2id\$v=19\$m=65536,t=3,p=4\$/random/'
client = TestClient(app)
r = client.get('/holdings', follow_redirects=False)
print('status:', r.status_code)
"
```

Expected: 303 (redirect to login — auth-protected). Not 500.

- [ ] **Step 18.5: Commit log review**

```bash
git log --oneline main..HEAD
```

Expected: ~17 commits in a clean linear progression matching tasks 1-17.

- [ ] **Step 18.6: Final integration commit (if any cleanup needed)**

If tests + ruff are already clean, no commit needed — the branch is ready for PR.

If anything failed: investigate, fix, commit:

```bash
git add <files>
git commit -m "fix(phase-5d): <specific cleanup>"
```

---

## Self-Review Notes

Spec coverage check:
- ✓ `holdings.sector` migration (Task 1)
- ✓ `sector.py` module + cache + bounded backfill (Task 2)
- ✓ `enrich_holdings` extended with sector/today_change_pct/sparkline (Task 3)
- ✓ `today_portfolio_change` / `contributors_ranked` / `sector_breakdown` (Tasks 4-6)
- ✓ POST→GET `/holdings/risk-analysis` (Task 7)
- ✓ `/holdings/export.csv` (Task 8)
- ✓ Route extension with KPI block (Task 9)
- ✓ Shell + layout CSS (Task 10)
- ✓ Hero + donut (Task 11)
- ✓ KPI strip (Task 12)
- ✓ Allocation / sector / contributors cards (Tasks 13-15)
- ✓ 14-col holdings table rewrite (Task 16)
- ✓ Monthly P&L card (Task 17)
- ✓ Integration verify (Task 18)

Type consistency:
- `kpi.today_change.{dollars,pct,up_count,down_count}` consistent across hero (Task 11) + service (Task 4).
- `kpi.ytd_realized` / `kpi.this_month_dividends` consistent across route (Task 9) + KPI strip (Task 12).
- `allocation` rows use `.market_value` / `.ticker` / `.pct` across donut (Task 11) + allocation card (Task 13).
- `sectors` rows use `.sector` / `.market_value` / `.pct` across sector card (Task 14).
- `contributors` rows use `.pl_dollars` / `.ticker` (inherited from enriched rows) in card (Task 15).
- `r.id` (int) for DELETE in table (Task 16), matching existing route signature.
