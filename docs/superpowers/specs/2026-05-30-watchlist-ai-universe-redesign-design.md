# /watchlist → AI Universe Management Page — Redesign Design

**Date:** 2026-05-30
**Status:** Approved for Implementation
**Scope:** `/watchlist` page only (P1 + P2). Home-dashboard split and P3 are out of scope.

---

## Problem & Repositioning

`/watchlist` is currently a bare CRUD table (代码 | 备注 | 删除): a flat, unsorted
list of 28 tickers with a dead "备注" column and large dead whitespace. It was
adequate when the watchlist was a 9-ticker config page. The system has since
changed: 28 tickers across 7 sectors, a nightly AI analysis job (Task #57), and a
Phase 5/6 evaluation system. The watchlist is now the **eval universe** — the
candidate pool the AI covers and the allocator trades from.

This is a **responsibility change, not a visual polish**:

> `/watchlist` = **AI Universe management page** — answers "which stocks is the AI
> covering, and is the universe balanced?" Not a quote page, not a trading page.

It shows: Universe · Sector · Coverage · latest Verdict · holding status.

## Roles (context, not scope)

- **Home** → "what happened today?" (future, separate)
- **Stock Detail** → "why this stock?" (existing)
- **Watchlist** → "which stocks is the AI covering?" (this spec)

---

## Locked Decisions (L1–L12)

| Lock | Decision |
|------|----------|
| **L1** | Remove `notes` entirely (UI + API + model + Alembic drop column). Zombie field, no consumer, sector grouping replaces its classification use. |
| **L2** | Watchlist uses **cached market data only**. No synchronous quote fetch, no per-card lazy fetch, no realtime requirement. Price/sparkline from `price_cache`. Fast first paint > quote freshness. |
| **L3** | **Shared card presentation partial, separate data presenters.** `/stock` keeps its realtime quote builder; `/watchlist` uses a `price_cache`-only presenter; both render through one shared card partial. |
| **L4** | **Verdict source = latest `EvaluationEvent` where `event_type='ai_analysis'`, using `subtype`.** NOT `AiAnalysis` (it has no subtype column). |
| **L5** | **Sector source is cache-only.** No yfinance / network `get_sector` during render. Uncached ticker → `Uncategorized`. |
| **L6** | `WatchlistCard` does **not** include company name. Card identity = ticker only. No live lookup, no new name cache this phase. |
| **L7** | Shared card partial supports **optional slots**. `/stock` may pass name + active + realtime price; `/watchlist` passes sector + verdict + status + cached price. |
| **L8** | **Refactor `/stock` last.** Visual-equivalence refactor only: no data-source change, no realtime behavior change; compare before/after screenshots. |
| **L9** | `watchlist_card.html` is a **dumb component**. Presenter outputs display-ready fields only; the partial does NO lookups, NO class-selection logic, NO branching on data source. |
| **L10** | Search is **client-only and visual-only**. It does not change DB ordering, grouping, or server query — only hides/shows already-rendered cards and groups. |
| **L11** | Add/delete returns the **full watchlist grid fragment** (sector counts, coverage, empty groups, dup status all depend on whole-view recomputation). |
| **L12** | **No drag-and-drop, no custom sectors, no user-configurable grouping.** Order is fully derived: `sector_count DESC → sector_name ASC → ticker ASC`, Uncategorized always last. Stateless and deterministic. Universe management, not personal curation. |

---

## Section 1 — Card data model (`WatchlistCard`)

Each watchlist ticker → one `WatchlistCard` view-model, assembled purely from
DB + cache (zero network, per L2/L5). The presenter computes **display-ready**
fields (L9):

| Field | Source | Missing |
|-------|--------|---------|
| `ticker` | `watchlist_items` | — |
| `price_display` | `price_cache` latest close, formatted | `"—"` |
| `change_display` | `price_cache` latest vs prior close, formatted `+0.8%` | `"—"` |
| `change_class` | sign of change → `mp-pos` / `mp-neg` / neutral | neutral |
| `sparkline` | `price_cache` last ~30 closes (raw list → `sparkpoints` filter) | empty (no spark) |
| `sector` | sector cache / `holdings.sector` (route uses for grouping) | `"Uncategorized"` |
| `verdict_class` | latest `EvaluationEvent(ai_analysis)` `subtype` → `mp-ai-badge--good/--bad/--neutral` | `mp-ai-badge--pending` |
| `verdict_label` | same → `Bullish` / `Neutral` / `Bearish` | `Pending` |
| `status_label` | `holdings`→`Holding`; open `paper_position`→`Paper Position`; else `Universe Only` | — |
| `status_class` | `mp-chip` variant per status | — |
| `active` | optional (stock sidebar highlight) | false |

**Card layout** (Variant A):
```
TICKER              price/change
Sector
[verdict chip] · [status badge]
sparkline
```
Missing price → `TICKER  —` / `Pending · Universe Only` / no sparkline.

---

## Section 2 — Architecture & boundaries (L3, L7–L9)

**New units**

1. **`marketpulse/web/watchlist_view.py`** — presenter (pure DB/cache → view-model,
   zero network):
   - `@dataclass WatchlistCard` (Section 1 fields, all display-ready).
   - `build_watchlist_view(session) -> WatchlistView` returning sector-grouped
     `[(sector_name, count, [WatchlistCard...]), ...]` ordered per L12, plus a
     `coverage` summary `{total, sectors, holdings, paper, universe_only}`.
   - Batch helpers (no N+1):
     - `_price_blocks(session, tickers)` ← `price_cache` (latest + prior close,
       last-30 closes for spark), greatest-per-group.
     - `_latest_verdicts(session, tickers)` ← `EvaluationEvent` greatest-per-group
       on `(ticker)` filtered `event_type='ai_analysis'`, newest `event_time` (L4).
     - `_status_map(session)` ← `holdings` ticker set + open `paper_position`
       (`status='OPEN'`) ticker set → Holding / Paper Position / Universe Only.
     - `_sector_map(tickers)`: for each ticker, `holdings.sector` if it is a
       holding, else the **cache-only** sector source — the sector JSON cache +
       YAML overrides (`backtest/sector.py::load_sector_cache` /
       `load_sector_overrides`), NEVER the network `holdings/sector.py::get_sector`.
       Uncached → `Uncategorized` (L5, zero network).

2. **`marketpulse/web/templates/partials/watchlist_card.html`** — shared dumb
   partial (L9). Renders one card from a card dict with optional slots (L7):
   `name?` (stock), `sector?`/`verdict?`/`status?` (watchlist), `active?` (stock).
   Variant A classes only (`mp-card`/`mp-ai-badge--*`/`mp-chip`/`mp-watchlist__spark`
   + `sparkpoints`). The card's **only** source of card markup.

3. **`marketpulse/web/templates/watchlist.html`** — rewrite. Variant A page header
   (`mp-card`, navy heading, Material Symbols) + batch-add textarea + client-side
   search box + coverage summary + sector-grouped card grids (each card = `include
   watchlist_card`).

**Modified units**

4. **`marketpulse/web/routes/watchlist.py`** — GET builds the view via the
   presenter; POST handles add (single + batch); DELETE; htmx fragments return the
   full grid (L11) + a result summary line for batch add.

5. **`marketpulse/web/templates/stock.html`** (refactor, done LAST per L8) — replace
   the inline `mp-watchlist__item` markup with `{% include partials/watchlist_card.html %}`.
   The `/stock` route maps its existing dict to the card's display-ready shape
   (presentation mapping only — no data-source change, no realtime change, L8).
   Verify visual equivalence via before/after screenshots.

**Boundary check:** presenter (data) ↔ partial (presentation) ↔ route
(composition). Each is independently testable: the presenter is `seeded DB →
view-model`, the partial is dumb rendering, the route composes.

---

## Section 3 — Features & interaction

**① Sector grouping (P1)** — presenter groups; route renders.
- Group order (L12): `card_count DESC → sector_name ASC`, `Uncategorized` forced
  last. Within a group: `ticker ASC`.
- Group header: `Technology · 6` (name + count).
- **Coverage summary** at top: `28 tickers · 7 sectors · 6 holdings · 6 paper ·
  16 universe-only` — the "is the universe balanced / which sector lacks samples"
  view that is this page's core value.

**② Search (P1)** — client-only, visual-only (L10).
- One search box; a minimal vanilla-JS filter hides cards whose `ticker` (and
  `sector`) don't match the query; a group with all cards hidden hides itself.
- Zero server round-trip, zero latency, no change to DB ordering/grouping.

**③ Batch add (P2)** — textarea replaces the single input (handles 1 or many).
- Accepts newline- **or** comma-separated tickers; submits htmx `POST /watchlist`
  with field `tickers`.
- Route: normalize (upper/strip) → dedup → validate each against `_TICKER_RE` →
  classify `{added, already_present, invalid}` → bulk-insert the new ones →
  return the **full re-rendered grid** (L11) + a one-line result
  (`added 5 · 3 already present · 1 invalid: XYZ`).
- **Partial success** (no whole-batch rollback): valid-new → insert; existing →
  report; invalid → report.

**④ Verdict chip + status badge (P2)** — both computed in the presenter (L9).
- verdict → `mp-ai-badge--good/--bad/--neutral/--pending` + label
  (Bullish/Neutral/Bearish/Pending).
- status → `mp-chip` + label (Holding / Paper Position / Universe Only).
- Card row: `[verdict chip] · [status badge]`.

**⑤ Delete** — per-card htmx delete; add/delete both return the full re-rendered
grid (L11) so counts, coverage, and empty groups stay correct.

---

## Section 4 — `notes` removal migration (L1)

1. **Model** — remove the `notes` column from `WatchlistItem` (`db/models.py`).
2. **Alembic migration** — drop column `notes` from `watchlist_items` using
   `op.batch_alter_table("watchlist_items") as batch: batch.drop_column("notes")`
   (SQLite-safe pattern matching existing migrations). Container entrypoint runs
   `alembic upgrade head` on deploy. **Zero data loss** — the column is entirely
   empty.
3. **Templates/refs** — `watchlist_row.html` is superseded by the card grid (its
   `item.notes` reference disappears). Grep the codebase to confirm no other
   `WatchlistItem.notes` reference (home/stock/route).
4. **Tests** — the test DB uses `Base.metadata.create_all` (model-driven), so it
   has no `notes` once the model drops it; the migration is verified separately by
   `alembic upgrade head` smoke against a copy of the prod schema.

---

## Section 5 — Testing strategy

**Presenter unit tests** (seeded DB, zero network):
- grouping + order: `count DESC → sector ASC`, `Uncategorized` last; within-group
  `ticker ASC`.
- verdict = latest `EvaluationEvent(ai_analysis)` `subtype` (L4): newest wins;
  maps to `--good/--bad/--neutral`; no event → `pending`.
- status badge: Holding vs Paper Position vs Universe Only (one case each).
- price/change/sparkline from `price_cache`; missing price → `"—"`, no spark.
- sector cache-only (L5): uncached → `Uncategorized`.
- coverage counts correct.

**Architecture guard** (enforces L2/L5): assert `watchlist_view.py` imports nothing
from DataService / quote-client / yfinance / the network `get_sector` — the
presenter is cache-only / zero-network (mirrors the Task #57 eval-only guard).

**Route tests** (client / db_session):
- GET `/watchlist` 200: grouped grid + coverage + cards (Variant A markers).
- POST batch add: newline/comma parse, dedup, validate, **partial success**
  (added/already/invalid), returns full grid fragment (L11).
- DELETE returns full grid fragment; counts update (L11).
- `notes` gone from responses; model has no `notes` attribute.

**Shared partial**: render test — all-slots payload vs minimal-slots payload.

**`/stock` visual equivalence (L8)**: a test asserting `/stock` still renders the
sidebar (the include works + key markers present); plus a **manual before/after
screenshot comparison** (not an automated pixel test).

---

## Out of scope (explicit, per L12 and scope lock)

- Home-dashboard split / changing `/` routing.
- Drag-and-drop ordering, custom sectors, user-configurable grouping, saved
  personal order.
- Realtime quotes, "refresh quotes" button, company-name display/cache.
- P3 items: coverage analytics dashboards.

## Rollout

1. Merge; Alembic `alembic upgrade head` drops `notes` on deploy (auto).
2. The page renders instantly from cache; sector backfill (existing/optional)
   warms the sector cache so fewer tickers fall into `Uncategorized` over time.
3. Verify `/stock` sidebar is visually unchanged after the shared-partial refactor.
