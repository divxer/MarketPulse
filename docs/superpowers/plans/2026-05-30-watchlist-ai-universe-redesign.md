# /watchlist AI Universe Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `/watchlist` from a bare CRUD ticker table into an AI-Universe management page — sector-grouped cards with coverage summary, latest AI verdict chip, holding-status badge, client-side search, and batch add — built on a shared dumb card partial and a cache-only, zero-network presenter.

**Architecture:** A pure presenter (`web/watchlist_view.py`) assembles display-ready `WatchlistCard`s from DB + caches only (price_cache, latest EvaluationEvent subtype, holdings/paper-position sets, sector cache) and groups them deterministically. A dumb shared partial (`partials/watchlist_card.html`) renders one card from display-ready fields. The route composes; add/delete return the full re-rendered grid. `notes` is dropped. `/stock`'s inline sidebar is refactored onto the shared partial LAST as a visual-equivalence change.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, Jinja2, htmx, Tailwind + NineScrolls Variant A (`mp-*` classes), Alembic, pytest. Tests run via `uv run pytest`; lint via `uv run ruff check`.

**Spec:** `docs/superpowers/specs/2026-05-30-watchlist-ai-universe-redesign-design.md` (commit `a835545`).
**Branch:** `feat/watchlist-universe-redesign` (already created off `main`).

---

## File Structure

| File | Responsibility |
|------|----------------|
| `marketpulse/web/watchlist_view.py` (NEW) | Presenter: `WatchlistCard`/`SectorGroup`/`Coverage`/`WatchlistView` dataclasses + `build_watchlist_view(session)` + cache-only helpers. Zero network. |
| `marketpulse/web/templates/partials/watchlist_card.html` (NEW) | Dumb shared card partial (optional slots). Variant A markup. |
| `marketpulse/web/templates/partials/watchlist_grid.html` (NEW) | Coverage summary + sector-grouped grid of cards. The fragment returned by GET/add/delete. |
| `marketpulse/web/templates/watchlist.html` (REWRITE) | Page shell: header, batch-add textarea, search box, `{% include watchlist_grid %}`. |
| `marketpulse/web/routes/watchlist.py` (MODIFY) | GET (presenter→grid), POST batch add (partial success→grid), DELETE (→grid). |
| `marketpulse/web/static/css/app.css` (MODIFY) | Minimal Variant A card-grid classes (`mp-wl-*`). |
| `marketpulse/db/models.py` (MODIFY) | Remove `WatchlistItem.notes`. |
| `alembic/versions/00XX_drop_watchlist_notes.py` (NEW) | `batch_alter_table` drop column. |
| `marketpulse/web/templates/stock.html` (MODIFY, LAST) | Replace inline `mp-watchlist__item` markup with the shared partial (visual-equivalence). |
| Tests | `tests/web/test_watchlist_view.py`, `tests/web/test_watchlist_routes.py`, `tests/architecture/test_watchlist_zero_network.py` |

**Shared dataclass contract** (defined in Task 1, referenced everywhere):

```python
@dataclass(frozen=True)
class WatchlistCard:
    ticker: str
    price_display: str        # "$450.24" or "—"
    change_display: str       # "+0.82%" / "-1.20%" / "—"
    change_class: str         # "mp-watchlist__chg--up" | "mp-watchlist__chg--down" | ""
    sparkline: list[float]    # raw closes; [] when <2 points
    sector: str               # group key; "Uncategorized" fallback
    verdict_class: str        # mp-ai-badge--good|--bad|--neutral|--pending
    verdict_label: str        # Bullish|Bearish|Neutral|Pending
    status_label: str         # Holding|Paper Position|Universe Only
    status_class: str         # mp-chip--success|--warn|--muted
    item_id: int | None = None  # watchlist_items.id (delete affordance); None on /stock
    active: bool = False
```

Verdict mapping (L4): `EvaluationEvent.subtype` "bullish"→(`mp-ai-badge--good`,"Bullish"), "bearish"→(`mp-ai-badge--bad`,"Bearish"), "neutral"→(`mp-ai-badge--neutral`,"Neutral"), none→(`mp-ai-badge--pending`,"Pending").
Status (L-status): ticker in `holdings`→("Holding",`mp-chip--success`); else open `paper_position`→("Paper Position",`mp-chip--warn`); else ("Universe Only",`mp-chip--muted`).

---

## Task 1: Presenter scaffolding + display helpers (pure, no DB)

**Files:**
- Create: `marketpulse/web/watchlist_view.py`
- Test: `tests/web/test_watchlist_view.py`

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_watchlist_view.py`:

```python
# Layer: test
"""Watchlist AI-Universe presenter."""
from __future__ import annotations

from marketpulse.web.watchlist_view import (
    WatchlistCard,
    _fmt_price,
    _fmt_change,
    _verdict_fields,
    _status_fields,
)


def test_fmt_price():
    assert _fmt_price(450.236) == "$450.24"
    assert _fmt_price(None) == "—"


def test_fmt_change():
    assert _fmt_change(450.0, 446.0) == ("+0.90%", "mp-watchlist__chg--up")
    assert _fmt_change(440.0, 446.0) == ("-1.35%", "mp-watchlist__chg--down")
    assert _fmt_change(450.0, None) == ("—", "")
    assert _fmt_change(None, 446.0) == ("—", "")
    assert _fmt_change(446.0, 0.0) == ("—", "")  # no div-by-zero


def test_verdict_fields():
    assert _verdict_fields("bullish") == ("mp-ai-badge--good", "Bullish")
    assert _verdict_fields("bearish") == ("mp-ai-badge--bad", "Bearish")
    assert _verdict_fields("neutral") == ("mp-ai-badge--neutral", "Neutral")
    assert _verdict_fields(None) == ("mp-ai-badge--pending", "Pending")


def test_status_fields():
    assert _status_fields("AAPL", {"AAPL"}, set()) == ("Holding", "mp-chip--success")
    assert _status_fields("QBTS", set(), {"QBTS"}) == ("Paper Position", "mp-chip--warn")
    assert _status_fields("SPY", set(), set()) == ("Universe Only", "mp-chip--muted")
    # holdings wins over paper if somehow both
    assert _status_fields("X", {"X"}, {"X"}) == ("Holding", "mp-chip--success")
```

- [ ] **Step 2: Run → FAIL** (`ModuleNotFoundError`).
Run: `uv run pytest tests/web/test_watchlist_view.py -v`

- [ ] **Step 3: Implement** `marketpulse/web/watchlist_view.py`:

```python
# Layer: web
"""Watchlist AI-Universe presenter — cache-only, ZERO network.

Assembles display-ready WatchlistCard view-models from DB + local caches only:
price_cache (price/sparkline), latest EvaluationEvent subtype (verdict),
holdings/paper-position sets (status), and the on-disk sector cache + YAML
overrides (sector grouping). Never calls a quote client, yfinance, or the
network get_sector — enforced by tests/architecture/test_watchlist_zero_network.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WatchlistCard:
    ticker: str
    price_display: str
    change_display: str
    change_class: str
    sparkline: list[float]
    sector: str
    verdict_class: str
    verdict_label: str
    status_label: str
    status_class: str
    active: bool = False


@dataclass(frozen=True)
class SectorGroup:
    name: str
    count: int
    cards: list[WatchlistCard]


@dataclass(frozen=True)
class Coverage:
    total: int
    sectors: int
    holdings: int
    paper: int
    universe_only: int


@dataclass(frozen=True)
class WatchlistView:
    groups: list[SectorGroup] = field(default_factory=list)
    coverage: Coverage = Coverage(0, 0, 0, 0, 0)


UNCATEGORIZED = "Uncategorized"

_VERDICT = {
    "bullish": ("mp-ai-badge--good", "Bullish"),
    "bearish": ("mp-ai-badge--bad", "Bearish"),
    "neutral": ("mp-ai-badge--neutral", "Neutral"),
}


def _fmt_price(close: float | None) -> str:
    return f"${close:,.2f}" if close is not None else "—"


def _fmt_change(latest: float | None, prior: float | None) -> tuple[str, str]:
    if latest is None or prior is None or prior == 0:
        return "—", ""
    pct = (latest - prior) / prior * 100.0
    cls = "mp-watchlist__chg--up" if pct >= 0 else "mp-watchlist__chg--down"
    return f"{'+' if pct >= 0 else ''}{pct:.2f}%", cls


def _verdict_fields(subtype: str | None) -> tuple[str, str]:
    return _VERDICT.get(subtype or "", ("mp-ai-badge--pending", "Pending"))


def _status_fields(
    ticker: str, holdings: set[str], paper: set[str],
) -> tuple[str, str]:
    if ticker in holdings:
        return "Holding", "mp-chip--success"
    if ticker in paper:
        return "Paper Position", "mp-chip--warn"
    return "Universe Only", "mp-chip--muted"
```

- [ ] **Step 4: Run → PASS.**
Run: `uv run pytest tests/web/test_watchlist_view.py -v`

- [ ] **Step 5: Commit**
```bash
git add marketpulse/web/watchlist_view.py tests/web/test_watchlist_view.py
git commit -m "feat(watchlist): presenter dataclasses + display helpers"
```

---

## Task 2: Cache-only data helpers (price / verdict / status / sector)

**Files:**
- Modify: `marketpulse/web/watchlist_view.py`
- Test: `tests/web/test_watchlist_view.py` (append)

CONTEXT: `PriceCacheEntry(ticker, date, open, high, low, close, volume, fetched_at)` PK `(ticker,date)`. `EvaluationEvent(event_type, subtype, ticker, event_time, ...)`. `Holding(ticker unique, sector, ...)`. `PaperPosition(ticker, status)`. Sector cache: `marketpulse.backtest.sector.load_sector_cache()` → `{ticker: sector}`, `load_sector_overrides()` → `{ticker: sector}` (both pure file reads, no network).

- [ ] **Step 1: Write the failing test** (append):

```python
from datetime import UTC, date, datetime
from decimal import Decimal

from marketpulse.db.models import (
    EvaluationEvent, Holding, PaperPosition, PriceCacheEntry, WatchlistItem,
)
from marketpulse.web.watchlist_view import (
    _price_blocks, _latest_verdicts, _status_sets,
)


def _add_price(s, ticker, d, close):
    s.add(PriceCacheEntry(ticker=ticker, date=d, open=close, high=close,
                          low=close, close=close, volume=1))


def test_price_blocks_latest_and_prior(db_session):
    _add_price(db_session, "AAPL", date(2026, 5, 28), 446.0)
    _add_price(db_session, "AAPL", date(2026, 5, 29), 450.0)
    db_session.commit()
    blocks = _price_blocks(db_session, ["AAPL", "ZZZZ"])
    assert blocks["AAPL"]["latest"] == 450.0
    assert blocks["AAPL"]["prior"] == 446.0
    assert blocks["AAPL"]["spark"][-2:] == [446.0, 450.0]
    assert "ZZZZ" not in blocks  # no rows → absent


def test_latest_verdicts_newest_wins(db_session):
    for st, t in [("neutral", datetime(2026, 5, 20, tzinfo=UTC)),
                  ("bullish", datetime(2026, 5, 29, tzinfo=UTC))]:
        db_session.add(EvaluationEvent(
            event_type="ai_analysis", subtype=st, ticker="AAPL",
            event_time=t, event_price=1.0, payload={}))
    db_session.commit()
    assert _latest_verdicts(db_session, ["AAPL"])["AAPL"] == "bullish"


def test_status_sets(db_session):
    db_session.add(Holding(ticker="AAPL", quantity=1, avg_cost=1))
    db_session.add(PaperPosition(
        ticker="QBTS", quantity=1, entry_price=Decimal("1"), status="OPEN",
        opened_at=datetime(2026, 5, 1, tzinfo=UTC), entry_date=date(2026, 5, 1),
        horizon_date=date(2026, 6, 1), strategy="x", order_id=9001))
    db_session.commit()
    holdings, paper = _status_sets(db_session)
    assert "AAPL" in holdings and "QBTS" in paper
```

NOTE: adjust the `PaperPosition(...)` kwargs to the real model's NOT-NULL columns (see `marketpulse/db/models.py::class PaperPosition`; reuse the helper from `tests/ai/test_eval_analysis.py` if present). Only `ticker` + `status="OPEN"` are semantically required by `_status_sets`.

- [ ] **Step 2: Run → FAIL.**
Run: `uv run pytest tests/web/test_watchlist_view.py -k "price_blocks or latest_verdicts or status_sets" -v`

- [ ] **Step 3: Implement** (append to `watchlist_view.py`):

```python
from sqlalchemy import and_, func, select

from marketpulse.backtest.sector import load_sector_cache, load_sector_overrides
from marketpulse.db.models import (
    EvaluationEvent, Holding, PaperPosition, PriceCacheEntry,
)

_SPARK_N = 30


def _price_blocks(session, tickers: list[str]) -> dict[str, dict]:
    """Per ticker: latest close, prior close, last-N closes (ascending)."""
    if not tickers:
        return {}
    rows = session.execute(
        select(PriceCacheEntry.ticker, PriceCacheEntry.date, PriceCacheEntry.close)
        .where(PriceCacheEntry.ticker.in_(tickers))
        .order_by(PriceCacheEntry.ticker, PriceCacheEntry.date.asc())
    ).all()
    closes: dict[str, list[float]] = {}
    for tkr, _d, close in rows:
        closes.setdefault(tkr, []).append(float(close))
    out: dict[str, dict] = {}
    for tkr, series in closes.items():
        out[tkr] = {
            "latest": series[-1],
            "prior": series[-2] if len(series) >= 2 else None,
            "spark": series[-_SPARK_N:],
        }
    return out


def _latest_verdicts(session, tickers: list[str]) -> dict[str, str]:
    """Latest EvaluationEvent(ai_analysis) subtype per ticker (newest wins)."""
    if not tickers:
        return {}
    latest = (
        select(EvaluationEvent.ticker,
               func.max(EvaluationEvent.event_time).label("mx"))
        .where(EvaluationEvent.event_type == "ai_analysis")
        .where(EvaluationEvent.ticker.in_(tickers))
        .group_by(EvaluationEvent.ticker)
        .subquery()
    )
    rows = session.execute(
        select(EvaluationEvent.ticker, EvaluationEvent.subtype)
        .join(latest, and_(EvaluationEvent.ticker == latest.c.ticker,
                           EvaluationEvent.event_time == latest.c.mx))
        .where(EvaluationEvent.event_type == "ai_analysis")
    ).all()
    return {t: st for t, st in rows}


def _status_sets(session) -> tuple[set[str], set[str]]:
    holdings = {t for (t,) in session.execute(select(Holding.ticker)).all()}
    paper = {t for (t,) in session.execute(
        select(PaperPosition.ticker).where(PaperPosition.status == "OPEN")
    ).all()}
    return holdings, paper


def _sector_map(tickers: list[str], holdings_sector: dict[str, str]) -> dict[str, str]:
    """Cache-only sector (L5): holdings.sector first, then on-disk cache + YAML
    overrides. NO network. Uncached → UNCATEGORIZED."""
    cache = load_sector_cache()
    overrides = load_sector_overrides()
    out: dict[str, str] = {}
    for t in tickers:
        out[t] = (holdings_sector.get(t) or overrides.get(t)
                  or cache.get(t) or UNCATEGORIZED)
    return out
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**
```bash
git add marketpulse/web/watchlist_view.py tests/web/test_watchlist_view.py
git commit -m "feat(watchlist): cache-only price/verdict/status/sector helpers"
```

---

## Task 3: `build_watchlist_view` — assemble, group, order, coverage

**Files:**
- Modify: `marketpulse/web/watchlist_view.py`
- Test: `tests/web/test_watchlist_view.py` (append)

- [ ] **Step 1: Write the failing test** (append):

```python
def _seed_universe(db_session):
    # 3 tickers: AAPL (holding, tech), MSFT (universe, tech), SPY (universe, ETF)
    for t in ["AAPL", "MSFT", "SPY"]:
        db_session.add(WatchlistItem(ticker=t))
    db_session.add(Holding(ticker="AAPL", quantity=1, avg_cost=1, sector="Technology"))
    _add_price(db_session, "AAPL", date(2026, 5, 28), 100.0)
    _add_price(db_session, "AAPL", date(2026, 5, 29), 101.0)
    db_session.add(EvaluationEvent(event_type="ai_analysis", subtype="bullish",
                   ticker="MSFT", event_time=datetime(2026, 5, 29, tzinfo=UTC),
                   event_price=1.0, payload={}))
    db_session.commit()


def test_build_view_groups_order_and_coverage(db_session, monkeypatch):
    import marketpulse.web.watchlist_view as wv
    # Force deterministic sectors (cache-only): MSFT→Technology, SPY→ETF
    monkeypatch.setattr(wv, "load_sector_cache", lambda: {"MSFT": "Technology", "SPY": "ETF"})
    monkeypatch.setattr(wv, "load_sector_overrides", lambda: {})
    _seed_universe(db_session)

    view = wv.build_watchlist_view(db_session)
    names = [g.name for g in view.groups]
    # Technology(2) before ETF(1); Uncategorized would be last if present
    assert names == ["Technology", "ETF"]
    tech = view.groups[0]
    assert tech.count == 2
    assert [c.ticker for c in tech.cards] == ["AAPL", "MSFT"]  # ticker ASC
    assert tech.cards[0].status_label == "Holding"
    assert tech.cards[0].price_display == "$101.00"
    assert tech.cards[1].verdict_label == "Bullish"            # MSFT
    assert tech.cards[1].status_label == "Universe Only"
    assert view.coverage.total == 3
    assert view.coverage.sectors == 2
    assert view.coverage.holdings == 1
    assert view.coverage.universe_only == 2


def test_build_view_uncategorized_last(db_session, monkeypatch):
    import marketpulse.web.watchlist_view as wv
    monkeypatch.setattr(wv, "load_sector_cache", lambda: {"MSFT": "Technology"})
    monkeypatch.setattr(wv, "load_sector_overrides", lambda: {})
    db_session.add(WatchlistItem(ticker="MSFT"))
    db_session.add(WatchlistItem(ticker="ZZZZ"))  # uncached → Uncategorized
    db_session.commit()
    view = wv.build_watchlist_view(db_session)
    assert [g.name for g in view.groups][-1] == "Uncategorized"
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `build_watchlist_view` (append):

```python
from marketpulse.db.models import WatchlistItem


def _card(ticker, block, subtype, sector, holdings, paper, item_id=None, active=False):
    latest = block["latest"] if block else None
    prior = block["prior"] if block else None
    change_display, change_class = _fmt_change(latest, prior)
    v_class, v_label = _verdict_fields(subtype)
    s_label, s_class = _status_fields(ticker, holdings, paper)
    return WatchlistCard(
        ticker=ticker,
        price_display=_fmt_price(latest),
        change_display=change_display,
        change_class=change_class,
        sparkline=block["spark"] if block else [],
        sector=sector,
        verdict_class=v_class,
        verdict_label=v_label,
        status_label=s_label,
        status_class=s_class,
        item_id=item_id,
        active=active,
    )


def build_watchlist_view(session) -> WatchlistView:
    id_map = {
        t: i for (i, t) in session.execute(
            select(WatchlistItem.id, WatchlistItem.ticker)
        ).all()
    }
    tickers = sorted(id_map)
    blocks = _price_blocks(session, tickers)
    verdicts = _latest_verdicts(session, tickers)
    holdings, paper = _status_sets(session)
    holdings_sector = {
        t: s for (t, s) in session.execute(
            select(Holding.ticker, Holding.sector)
        ).all() if s
    }
    sectors = _sector_map(tickers, holdings_sector)

    cards = [
        _card(t, blocks.get(t), verdicts.get(t), sectors[t], holdings, paper,
              item_id=id_map[t])
        for t in tickers
    ]
    by_sector: dict[str, list[WatchlistCard]] = {}
    for c in cards:
        by_sector.setdefault(c.sector, []).append(c)

    # Order: count DESC, then name ASC; Uncategorized always last (L12).
    def _key(name: str) -> tuple:
        is_uncat = name == UNCATEGORIZED
        return (is_uncat, -len(by_sector[name]), name)

    groups = [
        SectorGroup(name=n, count=len(by_sector[n]),
                    cards=sorted(by_sector[n], key=lambda c: c.ticker))
        for n in sorted(by_sector, key=_key)
    ]
    coverage = Coverage(
        total=len(tickers),
        sectors=len(by_sector),
        holdings=sum(1 for c in cards if c.status_label == "Holding"),
        paper=sum(1 for c in cards if c.status_label == "Paper Position"),
        universe_only=sum(1 for c in cards if c.status_label == "Universe Only"),
    )
    return WatchlistView(groups=groups, coverage=coverage)
```

- [ ] **Step 4: Run → PASS** (whole file).
Run: `uv run pytest tests/web/test_watchlist_view.py -v`
- [ ] **Step 5: Commit**
```bash
git add marketpulse/web/watchlist_view.py tests/web/test_watchlist_view.py
git commit -m "feat(watchlist): build_watchlist_view grouping + ordering + coverage"
```

---

## Task 4: Zero-network architecture guard

**Files:**
- Create: `tests/architecture/test_watchlist_zero_network.py`

- [ ] **Step 1: Write the test** (passes immediately on correct code):

```python
# Layer: test
"""L2/L5: the watchlist presenter must be cache-only / zero-network — it must
not import any quote client, yfinance, DataService, or the network get_sector."""
from __future__ import annotations

from pathlib import Path

_MODULE = (Path(__file__).resolve().parents[2]
           / "marketpulse" / "web" / "watchlist_view.py")

_FORBIDDEN = (
    "marketpulse.data.service",        # DataService (live quotes)
    "marketpulse.data.yfinance_client",
    "marketpulse.data.tencent_client",
    "marketpulse.data.hybrid_client",
    "marketpulse.holdings.sector",     # network get_sector(ticker)
    "import yfinance",
)


def _import_lines(path: Path) -> list[str]:
    return [s for ln in path.read_text().splitlines()
            if (s := ln.strip()).startswith(("import ", "from "))]


def test_watchlist_view_is_zero_network():
    lines = _import_lines(_MODULE)
    for forbidden in _FORBIDDEN:
        bad = [ln for ln in lines if forbidden in ln]
        assert not bad, f"watchlist_view must not import {forbidden}: {bad}"
```

- [ ] **Step 2: Run → PASS.** Run: `uv run pytest tests/architecture/test_watchlist_zero_network.py -v`
- [ ] **Step 3: Sanity-check it catches violations:** temporarily add `from marketpulse.data.service import DataService  # TEMP` to `watchlist_view.py`, run → FAIL, then REMOVE the temp line and confirm `git diff marketpulse/web/watchlist_view.py` is empty + test PASS.
- [ ] **Step 4: Commit**
```bash
git add tests/architecture/test_watchlist_zero_network.py
git commit -m "test(watchlist): zero-network presenter architecture guard"
```

---

## Task 5: Shared dumb card partial + grid partial + CSS

**Files:**
- Create: `marketpulse/web/templates/partials/watchlist_card.html`
- Create: `marketpulse/web/templates/partials/watchlist_grid.html`
- Modify: `marketpulse/web/static/css/app.css`

This task is pure markup/CSS (Variant A). Verify visually via the connected browser after Task 6 wires the route. No unit test here; covered by route tests (Task 6) + visual check.

- [ ] **Step 1: Create `partials/watchlist_card.html`** — dumb, optional slots (L7/L9). It consumes a `card` object with the `WatchlistCard` fields (and optional `name`):

```html
{# Dumb shared watchlist card (L9). Renders one card from display-ready fields.
   /watchlist passes a WatchlistCard (with item_id); /stock passes a dict with
   name + active (no item_id → no delete button). Root is a <div> so the delete
   button is not nested inside the <a> (invalid + double-trigger). #}
<div class="mp-wl-card{% if card.active %} is-active{% endif %}"
     data-ticker="{{ card.ticker }}" data-sector="{{ card.sector | default('') }}">
  <a href="/stock/{{ card.ticker }}" class="mp-wl-card__link">
    <div class="mp-wl-card__top">
      <span class="grotesk mp-wl-card__ticker">{{ card.ticker }}</span>
      <span class="mp-wl-card__px">
        <span class="mono tnum">{{ card.price_display }}</span>
        <span class="mono tnum {{ card.change_class }}">{{ card.change_display }}</span>
      </span>
    </div>
    {% if card.name %}<div class="mp-wl-card__name">{{ card.name }}</div>{% endif %}
    {% if card.sector %}<div class="mp-wl-card__sector">{{ card.sector }}</div>{% endif %}
    {% if card.verdict_label or card.status_label %}
    <div class="mp-wl-card__chips">
      {% if card.verdict_label %}<span class="mp-ai-badge {{ card.verdict_class }}">{{ card.verdict_label }}</span>{% endif %}
      {% if card.status_label %}<span class="mp-chip {{ card.status_class }}">{{ card.status_label }}</span>{% endif %}
    </div>
    {% endif %}
    {% if card.sparkline %}
    <svg class="mp-wl-card__spark" width="120" height="22" viewBox="0 0 120 22" preserveAspectRatio="none">
      <polyline points="{{ card.sparkline | sparkpoints(120, 22) }}" fill="none"
                stroke="{% if card.change_class == 'mp-watchlist__chg--down' %}var(--mp-down){% else %}var(--mp-up){% endif %}"
                stroke-width="1.5" />
    </svg>
    {% endif %}
  </a>
  {% if card.item_id %}
  <button class="mp-wl-card__del" title="移除"
          hx-delete="/watchlist/{{ card.item_id }}"
          hx-target="#mp-wl-grid" hx-swap="outerHTML">
    <span class="material-symbols-outlined">close</span>
  </button>
  {% endif %}
</div>
```

- [ ] **Step 2: Create `partials/watchlist_grid.html`** — coverage + grouped grids (the GET/add/delete fragment):

```html
{# Full watchlist grid fragment (returned by GET and after add/delete, L11). #}
<div id="mp-wl-grid">
  <div class="mp-wl-coverage">
    <span><b>{{ view.coverage.total }}</b> tickers</span>
    <span><b>{{ view.coverage.sectors }}</b> sectors</span>
    <span><b>{{ view.coverage.holdings }}</b> holdings</span>
    <span><b>{{ view.coverage.paper }}</b> paper</span>
    <span><b>{{ view.coverage.universe_only }}</b> universe-only</span>
  </div>
  {% if add_result %}<div class="mp-wl-result">{{ add_result }}</div>{% endif %}
  {% for group in view.groups %}
  <section class="mp-wl-group" data-group="{{ group.name }}">
    <h3 class="mp-wl-group__head">{{ group.name }} <span class="mp-wl-group__count">· {{ group.count }}</span></h3>
    <div class="mp-wl-grid">
      {% for card in group.cards %}{% include "partials/watchlist_card.html" %}{% endfor %}
    </div>
  </section>
  {% endfor %}
</div>
```

- [ ] **Step 3: Append minimal Variant A CSS** to `marketpulse/web/static/css/app.css` (reuse existing tokens `--mp-up`/`--mp-down`/navy; keep it small). Add a `.mp-wl-grid` responsive card grid, `.mp-wl-card` (mp-card-like surface, hover, `is-active`), `.mp-wl-coverage` (inline stat row), `.mp-wl-group__head` (navy Space-Grotesk heading), `.mp-wl-card__chips` (flex gap). Match the radius/shadow/spacing of existing `.mp-card`. Example skeleton:

```css
/* Watchlist AI-Universe page (mp-wl-*) — Variant A */
.mp-wl-coverage{display:flex;flex-wrap:wrap;gap:14px;font-size:13px;color:var(--mp-ink-soft);margin-bottom:14px}
.mp-wl-result{font-size:13px;color:var(--mp-ink);background:var(--mp-surface-2);border-radius:8px;padding:8px 12px;margin-bottom:12px}
.mp-wl-group{margin-bottom:18px}
.mp-wl-group__head{font-family:'Space Grotesk',sans-serif;color:var(--mp-navy);font-size:15px;font-weight:600;margin:0 0 8px}
.mp-wl-group__count{color:var(--mp-ink-soft);font-weight:400}
.mp-wl-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.mp-wl-card{position:relative;background:var(--mp-surface);border:1px solid var(--mp-border);border-radius:10px;transition:border-color .12s,box-shadow .12s}
.mp-wl-card:hover{border-color:var(--mp-navy);box-shadow:var(--mp-shadow-sm)}
.mp-wl-card.is-active{border-color:var(--mp-navy);background:var(--mp-surface-2)}
.mp-wl-card__link{display:block;padding:10px 12px;text-decoration:none;color:inherit}
.mp-wl-card__del{position:absolute;top:6px;right:6px;border:0;background:transparent;color:var(--mp-ink-soft);cursor:pointer;opacity:0;transition:opacity .12s;line-height:1}
.mp-wl-card:hover .mp-wl-card__del{opacity:1}
.mp-wl-card__del .material-symbols-outlined{font-size:16px}
.mp-wl-card__top{display:flex;justify-content:space-between;align-items:baseline}
.mp-wl-card__ticker{font-size:15px;font-weight:600;color:var(--mp-navy)}
.mp-wl-card__px{display:flex;gap:6px;font-size:12px}
.mp-wl-card__sector{font-size:11px;color:var(--mp-ink-soft);margin-top:2px}
.mp-wl-card__chips{display:flex;gap:6px;align-items:center;margin-top:6px}
.mp-wl-card__spark{display:block;width:100%;height:22px;margin-top:6px}
```

NOTE: the exact token variable names (`--mp-navy`, `--mp-surface`, `--mp-border`, `--mp-shadow-sm`, `--mp-ink-soft`, `--mp-up`, `--mp-down`) must be confirmed against the top of `app.css` / `ns-tokens.css`; substitute the real ones. Reuse, don't invent palette values.

- [ ] **Step 4: Commit** (visual verification happens in Task 6):
```bash
git add marketpulse/web/templates/partials/watchlist_card.html marketpulse/web/templates/partials/watchlist_grid.html marketpulse/web/static/css/app.css
git commit -m "feat(watchlist): shared dumb card partial + grid partial + Variant A CSS"
```

---

## Task 6: Route GET + page rewrite + client search

**Files:**
- Modify: `marketpulse/web/routes/watchlist.py`
- Modify: `marketpulse/web/templates/watchlist.html`
- Test: `tests/web/test_watchlist_routes.py` (create)

- [ ] **Step 1: Write the failing test** `tests/web/test_watchlist_routes.py`:

```python
# Layer: test
"""Watchlist route — GET renders the AI-Universe grid."""
from __future__ import annotations

from datetime import UTC, date, datetime

from marketpulse.db.models import EvaluationEvent, PriceCacheEntry, WatchlistItem


def _login(client, monkeypatch):
    from marketpulse.auth.password import hash_password
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password("secret"))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": "secret"})


def _seed(db_url):
    from marketpulse.db import base as db_base
    gen = db_base.session_scope(); s = next(gen)
    s.add(WatchlistItem(ticker="MSFT"))
    s.add(PriceCacheEntry(ticker="MSFT", date=date(2026, 5, 29), open=1, high=1,
                          low=1, close=450.0, volume=1))
    s.add(EvaluationEvent(event_type="ai_analysis", subtype="bullish",
          ticker="MSFT", event_time=datetime(2026, 5, 29, tzinfo=UTC),
          event_price=1.0, payload={}))
    s.commit(); gen.close()


def test_watchlist_get_renders_grid(client, db_url, monkeypatch):
    _login(client, monkeypatch)
    _seed(db_url)
    res = client.get("/watchlist")
    assert res.status_code == 200
    body = res.text
    assert "mp-wl-grid" in body
    assert "MSFT" in body
    assert "Bullish" in body
    assert "Universe Only" in body
    assert "tickers" in body  # coverage summary
    assert "备注" not in body  # notes column gone
```

- [ ] **Step 2: Run → FAIL** (old page has no `mp-wl-grid`).
Run: `uv run pytest tests/web/test_watchlist_routes.py::test_watchlist_get_renders_grid -v`

- [ ] **Step 3: Rewrite the GET route** in `marketpulse/web/routes/watchlist.py`:

```python
from marketpulse.web.watchlist_view import build_watchlist_view

@router.get("/watchlist", response_class=HTMLResponse)
def watchlist_page(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    view = build_watchlist_view(db)
    return templates.TemplateResponse(
        request, "watchlist.html", {"view": view, "add_result": None})
```

- [ ] **Step 4: Rewrite `marketpulse/web/templates/watchlist.html`**:

```html
{% extends "base.html" %}
{% block content %}
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">visibility</span>
      AI Universe
    </span>
    <input id="mp-wl-search" type="search" placeholder="搜索 ticker / sector"
           class="border rounded px-3 py-1 text-sm" oninput="mpWlFilter(this.value)">
  </div>

  <form hx-post="/watchlist" hx-target="#mp-wl-grid" hx-swap="outerHTML"
        class="flex gap-2 mb-4 items-start">
    <textarea name="tickers" rows="2" placeholder="MSFT, GOOGL  或每行一个"
              class="border rounded px-3 py-1 uppercase flex-1 text-sm"></textarea>
    <button class="mp-btn mp-btn--navy mp-btn--sm">批量添加</button>
  </form>

  {% include "partials/watchlist_grid.html" %}
</section>

<script>
function mpWlFilter(q){
  q=(q||'').trim().toUpperCase();
  document.querySelectorAll('#mp-wl-grid .mp-wl-card').forEach(function(c){
    var hay=(c.dataset.ticker+' '+(c.dataset.sector||'')).toUpperCase();
    c.style.display = (!q || hay.indexOf(q)>=0) ? '' : 'none';
  });
  document.querySelectorAll('#mp-wl-grid .mp-wl-group').forEach(function(g){
    var any=g.querySelector('.mp-wl-card:not([style*="display: none"])');
    g.style.display = any ? '' : 'none';
  });
}
</script>
{% endblock %}
```

- [ ] **Step 5: Run → PASS.** Run: `uv run pytest tests/web/test_watchlist_routes.py::test_watchlist_get_renders_grid -v`
- [ ] **Step 6: Visual check** via connected browser: navigate `http://192.168.50.29:8088/watchlist` (after deploy) OR run locally; confirm grouped cards, coverage row, chips, sparklines, search filters live. (Manual; not a blocker for commit if the route test passes.)
- [ ] **Step 7: Commit**
```bash
git add marketpulse/web/routes/watchlist.py marketpulse/web/templates/watchlist.html tests/web/test_watchlist_routes.py
git commit -m "feat(watchlist): GET route + AI-Universe page rewrite + client search"
```

---

## Task 7: Batch add (partial success) → full grid fragment

**Files:**
- Modify: `marketpulse/web/routes/watchlist.py`
- Test: `tests/web/test_watchlist_routes.py` (append)

- [ ] **Step 1: Write the failing test** (append):

```python
def test_watchlist_batch_add_partial_success(client, db_url, monkeypatch):
    _login(client, monkeypatch)
    _seed(db_url)  # MSFT already present
    res = client.post("/watchlist", data={"tickers": "msft, GOOGL\nNVDA\n@@bad"})
    assert res.status_code == 200
    body = res.text
    assert "mp-wl-grid" in body          # full grid fragment (L11)
    assert "GOOGL" in body and "NVDA" in body
    # result line reports all three classes
    assert "added 2" in body.lower() or "added&nbsp;2" in body.lower()
    assert "already" in body.lower()     # MSFT existed
    assert "invalid" in body.lower()     # @@bad
```

- [ ] **Step 2: Run → FAIL** (POST still returns old single-row partial).
- [ ] **Step 3: Rewrite the POST route**:

```python
def _parse_tickers(raw: str) -> list[str]:
    parts = raw.replace(",", "\n").split("\n")
    seen, out = set(), []
    for p in parts:
        t = p.strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


@router.post("/watchlist", response_class=HTMLResponse)
def watchlist_add(
    request: Request,
    tickers: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    existing = {t for (t,) in db.query(WatchlistItem.ticker).all()}
    added, already, invalid = [], [], []
    for t in _parse_tickers(tickers):
        if not _TICKER_RE.match(t):
            invalid.append(t)
        elif t in existing:
            already.append(t)
        else:
            db.add(WatchlistItem(ticker=t)); existing.add(t); added.append(t)
    db.commit()
    parts = [f"added {len(added)}"]
    if already:
        parts.append(f"{len(already)} already present")
    if invalid:
        parts.append(f"{len(invalid)} invalid: {', '.join(invalid)}")
    view = build_watchlist_view(db)
    return templates.TemplateResponse(
        request, "partials/watchlist_grid.html",
        {"view": view, "add_result": " · ".join(parts)})
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**
```bash
git add marketpulse/web/routes/watchlist.py tests/web/test_watchlist_routes.py
git commit -m "feat(watchlist): batch add with partial success → full grid fragment"
```

---

## Task 8: Delete → full grid fragment

**Files:**
- Modify: `marketpulse/web/routes/watchlist.py`
- Test: `tests/web/test_watchlist_routes.py` (append)

- [ ] **Step 1: Write the failing test** (append):

```python
def test_watchlist_delete_returns_grid(client, db_url, monkeypatch):
    _login(client, monkeypatch)
    _seed(db_url)
    from marketpulse.db import base as db_base
    gen = db_base.session_scope(); s = next(gen)
    item_id = s.query(WatchlistItem).filter_by(ticker="MSFT").one().id
    gen.close()
    res = client.delete(f"/watchlist/{item_id}")
    assert res.status_code == 200
    assert "mp-wl-grid" in res.text       # full grid fragment (L11)
    assert "MSFT" not in res.text
```

- [ ] **Step 2: Run → FAIL** (delete returns outerHTML row removal, not grid).
- [ ] **Step 3: Rewrite the DELETE route** to re-render the grid:

```python
@router.delete("/watchlist/{item_id}", response_class=HTMLResponse)
def watchlist_delete(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    db.query(WatchlistItem).filter(WatchlistItem.id == item_id).delete()
    db.commit()
    view = build_watchlist_view(db)
    return templates.TemplateResponse(
        request, "partials/watchlist_grid.html",
        {"view": view, "add_result": None})
```

NOTE: the delete button already exists in `watchlist_card.html` (Task 5), rendered only when `card.item_id` is set — `/watchlist` cards carry `item_id` (threaded via `build_watchlist_view`'s `id_map`), `/stock` sidebar dicts do not, so the sidebar shows no delete button. This task only needs the DELETE route to re-render the grid. No dataclass change needed here (`item_id` is already on `WatchlistCard` from Task 1).

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**
```bash
git add marketpulse/web/routes/watchlist.py marketpulse/web/templates/partials/watchlist_card.html marketpulse/web/watchlist_view.py tests/web/test_watchlist_routes.py
git commit -m "feat(watchlist): delete re-renders full grid fragment"
```

---

## Task 9: Drop `notes` (model + Alembic migration)

**Files:**
- Modify: `marketpulse/db/models.py` (remove `WatchlistItem.notes`)
- Create: `alembic/versions/00XX_drop_watchlist_notes.py`
- Test: `tests/web/test_watchlist_routes.py` (append a guard)

- [ ] **Step 1: Write the failing test** (append):

```python
def test_watchlistitem_has_no_notes():
    from marketpulse.db.models import WatchlistItem
    assert not hasattr(WatchlistItem, "notes")
```

- [ ] **Step 2: Run → FAIL** (model still has `notes`).
- [ ] **Step 3: Remove the column** — delete the `notes:` line from `class WatchlistItem` in `marketpulse/db/models.py`.
- [ ] **Step 4: Create the migration.** Find current head: `uv run alembic heads`. Create `alembic/versions/00XX_drop_watchlist_notes.py` (replace `<HEAD>` with the real down_revision):

```python
"""drop watchlist_items.notes (zombie field)

Revision ID: dropwlnotes01
Revises: <HEAD>
"""
from alembic import op

revision = "dropwlnotes01"
down_revision = "<HEAD>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("watchlist_items") as batch:
        batch.drop_column("notes")


def downgrade() -> None:
    import sqlalchemy as sa
    with op.batch_alter_table("watchlist_items") as batch:
        batch.add_column(sa.Column("notes", sa.Text(), nullable=True))
```

- [ ] **Step 5: Run → PASS** + confirm no other refs:
Run: `uv run pytest tests/web/test_watchlist_routes.py::test_watchlistitem_has_no_notes -v`
Run: `grep -rn "WatchlistItem.notes\|item.notes\|\.notes" marketpulse/web/templates/ marketpulse/web/routes/watchlist.py` → expect no matches (the old `watchlist_row.html` `item.notes` is gone with the rewrite; delete the now-unused `partials/watchlist_row.html` if nothing references it: `grep -rn "watchlist_row" marketpulse/`).
- [ ] **Step 6: Verify migration applies** on a scratch DB:
Run: `uv run alembic upgrade head` (against a throwaway sqlite via `DATABASE_URL=sqlite:////tmp/mp_mig.db uv run alembic upgrade head`) → no error.
- [ ] **Step 7: Commit**
```bash
git add marketpulse/db/models.py alembic/versions/00XX_drop_watchlist_notes.py tests/web/test_watchlist_routes.py
git rm marketpulse/web/templates/partials/watchlist_row.html 2>/dev/null || true
git commit -m "feat(watchlist): drop zombie notes column (model + alembic batch migration)"
```

---

## Task 10: Refactor `/stock` sidebar onto the shared partial (LAST — L8)

**Files:**
- Modify: `marketpulse/web/templates/stock.html`
- Modify: `marketpulse/web/routes/stock.py` (map sidebar dicts → card shape)
- Test: `tests/web/` existing stock test (assert sidebar still renders)

VISUAL-EQUIVALENCE ONLY (L8): no data-source change. The `/stock` route keeps its
realtime quote builder; only the per-item dict is mapped to the partial's
display-ready shape, and the inline markup is replaced by the include.

- [ ] **Step 1: Capture BEFORE screenshot** of `/stock/MSFT` sidebar via the connected browser (save to disk).
- [ ] **Step 2: Map the sidebar dict** in `stock.py` to the partial's fields. Where `watchlist_items` dicts are built (around lines 84–115), produce keys the partial reads: `ticker`, `name`, `active` (rename from `is_active`), and display-ready `price_display`/`change_display`/`change_class`/`sparkline`. Reuse `watchlist_view._fmt_price` / `_fmt_change`:

```python
from marketpulse.web.watchlist_view import _fmt_price, _fmt_change
# when appending each sidebar item:
chg_display, chg_class = _fmt_change(wl_quote.price, prev_close)  # prev from bars[-2]
watchlist_items.append({
    "ticker": wl_ticker,
    "name": <existing name or "">,
    "active": wl_ticker == ticker,
    "price_display": _fmt_price(wl_quote.price),
    "change_display": chg_display,
    "change_class": chg_class,
    "sparkline": spark,
})
```
(Keep the existing try/except fallback; on failure emit `price_display="—"`, `change_display="—"`, `change_class=""`, `sparkline=[]`.)

- [ ] **Step 3: Replace the inline `<ul class="mp-watchlist">…` block** in `stock.html` with the shared partial inside the same `<aside class="mp-card">` container:

```html
<div class="mp-wl-grid" style="grid-template-columns:1fr">
  {% for card in watchlist_items %}{% include "partials/watchlist_card.html" %}{% endfor %}
</div>
```
(The partial omits sector/verdict/status because the stock dict doesn't set them — L7 optional slots. `name` + `active` render.)

- [ ] **Step 4: Run the stock route test + full web tests**:
Run: `uv run pytest tests/web/ -q` → all pass (no regression). If a stock test asserted old `mp-watchlist__item` markers, update it to assert the sidebar still lists the tickers (`/stock/MSFT` page contains the other watchlist tickers + links).
- [ ] **Step 5: Capture AFTER screenshot** and compare to Step 1 — confirm visual equivalence (ticker, price, change color, sparkline, active highlight). Note any delta; iterate CSS if needed.
- [ ] **Step 6: Commit**
```bash
git add marketpulse/web/templates/stock.html marketpulse/web/routes/stock.py tests/web/
git commit -m "refactor(stock): sidebar onto shared watchlist card partial (visual-equivalence)"
```

---

## Task 11: Final integration

- [ ] **Step 1: Full suite** — `uv run pytest -q` → all green.
- [ ] **Step 2: Lint** — `uv run ruff check marketpulse/ tests/` → clean.
- [ ] **Step 3: Route smoke** — `uv run python -c "from marketpulse.web.main import create_app; create_app()"` imports OK; presenter import OK.
- [ ] **Step 4: Visual sweep** (connected browser, after deploy or local run): `/watchlist` grouped cards + coverage + chips + sparklines + search + batch add + delete; `/stock/MSFT` sidebar unchanged.
- [ ] **Step 5: Commit any fixups**
```bash
git add -A && git commit -m "chore(watchlist): final integration — suite + ruff + smoke" || echo "nothing to commit"
```

---

## Notes for the implementer

- **Zero-network (L2/L5):** `watchlist_view.py` must never import a quote client / yfinance / DataService / the network `holdings/sector.py::get_sector`. The Task 4 guard enforces it.
- **Verdict source (L4):** latest `EvaluationEvent(event_type='ai_analysis')` `subtype` per ticker — NOT `AiAnalysis`.
- **Dumb partial (L9):** the card template branches only on field PRESENCE (`{% if card.sector %}`), never computes classes or queries.
- **Ordering (L12):** `count DESC → sector ASC`, `Uncategorized` last; within group `ticker ASC`. Fully derived, no persistence.
- **`/stock` last (L8):** visual-equivalence only; screenshot before/after.
- **Variant A:** reuse `mp-card`, `mp-ai-badge--*`, `mp-chip`, `mp-btn--navy`, `sparkpoints`, and real `--mp-*` CSS vars from `app.css`/`ns-tokens.css`. Add only the small `.mp-wl-*` grid/card classes.
- **`PaperPosition` test construction:** match real NOT-NULL columns (see `class PaperPosition` / reuse `tests/ai/test_eval_analysis.py` helper).
- **`mp-chip--muted` / `mp-chip--success` / `mp-chip--warn`:** confirm these exist in `app.css`; if a variant is missing, add a minimal one in Variant A style (don't invent new palette).
