# Phase 5b-2 · Stock Detail · NineScrolls Variant A — Full Visual + Data

**Status:** Draft — awaiting user review
**Author:** harvey
**Date:** 2026-05-15

## Goal

Complete the `/stock/{ticker}` redesign by applying the full NineScrolls Variant A visual language + wiring real watchlist data into the left rail + porting chart theme colors. Phase 5b-1 (PR #30) already shipped the 3-column skeleton; this PR makes it look like the mockup.

## Scope

Single PR containing all of:

1. **Watchlist sidebar (left rail)** — real data, replaces the "Phase 5b-2" placeholder
2. **NineScrolls visual classes** — `.mp-card`, `.mp-chip`, `.mp-table`, `.mp-eyebrow`, `.mp-btn`, `.mp-rule` applied across the page
3. **Typography + color tokens** — Space Grotesk for h1/h2, mono for numerics, navy for headings, primary for actions
4. **Material Symbols icons** — replace emoji (📝🤖⚠☆★) with `<span class="material-symbols-outlined">`
5. **80×4 section rule** — under the page's symbol strip h1
6. **Chart theme port** — extract colors from `chart-svg.jsx > themeColors()` into `marketpulse/web/static/chart-theme.js`, consumed by `chart.js`

## Architecture

### 1. Watchlist sidebar

**Backend** — `routes/stock.py::stock_page`:

Add a watchlist sidebar fetch:

```python
# Pull all watchlist tickers (already-sorted by created_at)
watchlist_tickers = (
    db.query(WatchlistItem.ticker)
    .order_by(WatchlistItem.created_at.asc())
    .all()
)
watchlist_items: list[dict] = []
for (wl_ticker,) in watchlist_tickers:
    try:
        q = data.get_quote(wl_ticker)  # cached by QUOTE_CACHE (60s TTL)
        # Sparkline: 30 bars. Tencent path is fast; degrade gracefully.
        try:
            bars30 = data.get_history(wl_ticker, period="30d")
            spark = [b.close for b in bars30[-30:]]
        except Exception:
            spark = []
        watchlist_items.append({
            "ticker": wl_ticker,
            "name": q.name or wl_ticker,  # fall back if no display name
            "price": q.price,
            "change_pct": q.change_pct,
            "sparkline": spark,  # list of floats
            "is_active": wl_ticker == ticker,
        })
    except Exception as exc:
        log.warning(
            "stock_page_watchlist_quote_failed",
            ticker=wl_ticker, error=str(exc),
        )
        # Show ticker without price rather than crash
        watchlist_items.append({
            "ticker": wl_ticker,
            "name": wl_ticker,
            "price": None,
            "change_pct": None,
            "sparkline": [],
            "is_active": wl_ticker == ticker,
        })
```

Pass `watchlist_items` to the template.

**Template** — left rail in `stock.html`:

```html
<aside class="mp-card" style="height: fit-content;">
  <div class="mp-card__head" style="padding: 12px 14px;">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">visibility</span>
      自选股
    </span>
    <span class="text-xs text-slate-500">{{ watchlist_items | length }}</span>
  </div>
  <ul class="mp-watchlist">
    {% for item in watchlist_items %}
    <li class="mp-watchlist__item {% if item.is_active %}is-active{% endif %}">
      <a href="/stock/{{ item.ticker }}" class="mp-watchlist__link">
        <div class="mp-watchlist__row">
          <div class="mp-watchlist__id">
            <div class="grotesk mp-watchlist__ticker">{{ item.ticker }}</div>
            <div class="mp-watchlist__name">{{ item.name }}</div>
          </div>
          {% if item.sparkline %}
          <svg class="mp-watchlist__spark" width="56" height="20" viewBox="0 0 56 20" preserveAspectRatio="none">
            <polyline points="{{ item.sparkline | sparkpoints(56, 20) }}"
                      fill="none" stroke="{{ 'var(--mp-up)' if item.change_pct >= 0 else 'var(--mp-down)' }}"
                      stroke-width="1.5" />
          </svg>
          {% endif %}
        </div>
        <div class="mp-watchlist__row mp-watchlist__row--values">
          {% if item.price is not none %}
          <span class="mono tnum mp-watchlist__price">{{ "%.2f" | format(item.price) }}</span>
          <span class="mono tnum {% if item.change_pct >= 0 %}mp-watchlist__chg--up{% else %}mp-watchlist__chg--down{% endif %}">
            {% if item.change_pct >= 0 %}+{% endif %}{{ "%.2f" | format(item.change_pct) }}%
          </span>
          {% else %}
          <span class="text-xs text-slate-400">数据不可用</span>
          {% endif %}
        </div>
      </a>
    </li>
    {% else %}
    <li class="px-3 py-4 text-xs text-slate-500 text-center">
      还没添加自选股 — 在任意股票页面点 ☆
    </li>
    {% endfor %}
  </ul>
</aside>
```

A custom Jinja filter `sparkpoints(width, height)` converts the floats list to SVG `points` attribute. Lives in `marketpulse/web/main.py` alongside the existing `_render_markdown` filter:

```python
def _sparkpoints(values: list[float], width: int, height: int) -> str:
    """Convert a values list to SVG polyline points attribute.
    Linearly normalizes to fit [0, width] × [0, height], inverting Y."""
    if not values or len(values) < 2:
        return ""
    lo = min(values)
    hi = max(values)
    if hi == lo:
        # Flat line — return horizontal mid-line
        return " ".join(f"{i*width/(len(values)-1):.1f},{height/2:.1f}" for i in range(len(values)))
    span = hi - lo
    pts = []
    for i, v in enumerate(values):
        x = i * width / (len(values) - 1)
        # Invert Y: higher value → smaller y (closer to top)
        y = height - (v - lo) / span * height
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


templates.env.filters["sparkpoints"] = _sparkpoints
```

Out of scope: tabs (美股/A股/ETF), drag-reorder, search bar at top.

### 2. Visual classes applied across the page

Walk the existing `stock.html` and replace ad-hoc Tailwind with NineScrolls primitives:

| Element | Before | After |
|---|---|---|
| Symbol strip | `<header class="flex flex-wrap items-baseline...">` | Wrap in `<header class="mp-symbol-strip">` (new class in app.css? OR style inline like mockup) |
| Price display | `<span class="text-2xl font-semibold">` | `<span class="mono tnum" style="font-size: 44px; font-weight: 600; color: var(--ns-navy);">` |
| Position card | `<div class="px-3 py-2 bg-slate-50 rounded text-sm">` | `<section class="mp-card"><div class="mp-card__head">…</div><div class="mp-card__body">…</div></section>` |
| Record-trade form | `<form class="px-3 py-2 bg-amber-50 rounded text-sm">` | `<form class="mp-card mp-card--accent">…</form>` (or just `.mp-card`) |
| AI analysis div | `<div id="analysis">` | Wrap inside `<section class="mp-card">` |
| News list | `<div class="px-3 py-2 bg-slate-50 rounded text-sm">` | `<section class="mp-card">` |
| Recent trades | `<table class="mt-2 text-sm w-full">` | `<table class="mp-table">` |
| Period buttons | `<button class="px-2 py-1 rounded border border-slate-200">` | `<button class="mp-seg-btn">` inside `<div class="mp-seg">` |
| AI action button | `<button class="bg-slate-900 text-white px-3 py-1 rounded">` | `<button class="mp-btn mp-btn--navy">` |
| Watch button | `<button class="px-2 py-1 rounded border ...">` | `<button class="mp-btn mp-btn--ghost">` |
| ⚠ stale badge | `<span class="text-amber-600 text-xs">⚠ 缓存</span>` | `<span class="mp-chip mp-chip--warn"><span class="material-symbols-outlined">warning</span> 缓存</span>` |

The `.mp-card`, `.mp-chip`, etc. are already defined in `static/css/app.css` (from Phase 5a — NineScrolls overlay). We just apply them.

### 3. Material Symbols icon mapping

| Emoji | Material Symbol |
|---|---|
| 📝 | `edit_note` |
| 🤖 | `auto_awesome` |
| ⚠ | `warning` |
| ☆ | `star_border` |
| ★ | `star` |
| (new) | `visibility` (watchlist eye) |

Usage: `<span class="material-symbols-outlined">edit_note</span>`. Font already loaded from Phase 5a (Google Fonts link in base.html).

### 4. Section header rules (80×4)

Applied **only** to the page's main h1 (the ticker symbol). Add directly after the symbol strip:

```html
<div class="mp-rule"></div>
```

`.mp-rule` is already in `app.css`.

### 5. Chart theme port

**New file** `marketpulse/web/static/chart-theme.js`:

```javascript
// MarketPulse — chart color tokens (Variant A · NineScrolls light theme).
// Mirrors the SVG mockup palette (chart-svg.jsx > themeColors()).
// production chart.js imports these constants into lightweight-charts options.

window.MP_CHART_THEME = {
  // Background and grid
  background: "#ffffff",
  textColor: "#022448",            // navy
  borderColor: "#c1c6d5",          // ns-outline-variant
  gridLines: "#efecff",            // ns-surface-container

  // Candle bodies
  upColor:        "#0e8a5f",       // mp-up
  downColor:      "#c0392b",       // mp-down
  upBorder:       "#0a6b48",       // mp-up-deep
  downBorder:     "#8b251c",       // mp-down-deep
  wickUpColor:    "#0e8a5f",
  wickDownColor:  "#c0392b",

  // Indicator lines on main chart
  ema12: "#0066cc",                // ns-primary (was light blue #0ea5e9)
  ema26: "#f59e0b",                // amber (unchanged — good contrast)
  sma50: "#8b5cf6",                // purple (unchanged)
  sma200: "#022448",               // navy (was slate gray; navy reads more serious)
  bbUpper: "#a855f7",              // purple, dashed
  bbLower: "#a855f7",

  // RSI
  rsiLine: "#0066cc",              // primary (was purple)
  rsiOverbought: "#fca5a5",
  rsiOversold: "#93c5fd",

  // MACD
  macdLine: "#0066cc",             // primary
  macdSignal: "#f59e0b",           // amber
  macdHistPositive: "rgba(14,138,95,0.6)",
  macdHistNegative: "rgba(192,57,43,0.6)",

  // Markers
  signalGoldenCross: "#16a34a",    // green
  signalDeathCross:  "#dc2626",    // red
  signalOverbought:  "#f59e0b",
  signalOversold:    "#3b82f6",
  signalBollingerUpper: "#a855f7",
  signalBollingerLower: "#6366f1",
};
```

**Modify `chart.js`** — use `window.MP_CHART_THEME` constants instead of hardcoded hex values:

Replace lines like `color: "#0ea5e9"` (EMA12) with `color: window.MP_CHART_THEME.ema12`. Same pattern for ALL color literals in chart.js — extract them all to consume from the theme.

**Load order** in stock.html:

```html
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.2/..."></script>
<script src="/static/chart-theme.js"></script>  <!-- BEFORE chart.js -->
<script src="/static/chart.js"></script>
```

## Files

**New:**
- `marketpulse/web/static/chart-theme.js`
- `tests/web/test_stock_visual.py` (optional — assertions on key visual elements)

**Modified:**
- `marketpulse/web/routes/stock.py` — add watchlist_items fetch
- `marketpulse/web/main.py` — add `_sparkpoints` Jinja filter
- `marketpulse/web/templates/stock.html` — full visual rewrite per mockup
- `marketpulse/web/static/chart.js` — use MP_CHART_THEME constants
- `marketpulse/web/static/css/app.css` — add `.mp-watchlist*` classes if not already present (verify; otherwise add minimal styling)

**Unchanged:**
- Phase 5b-1's grid structure (kept)
- Tailwind classes that don't conflict with NineScrolls (mostly grid utilities, spacing)
- All chart logic (lazy-load, OHLC sync, period buttons, indicators)

## Edge Cases

| Case | Behavior |
|---|---|
| Empty watchlist | Render "还没添加自选股 — 在任意股票页面点 ☆" empty-state in left rail |
| Watchlist quote fetch fails for some tickers | Show ticker name + "数据不可用" caption for failed; others render normally |
| Sparkline data missing | Skip the SVG; show only price + % chg in that row |
| User is on a ticker not in their watchlist | Watchlist renders normally; no item is `is-active` |
| Watchlist has 50+ tickers | Render all (no truncation in v1); long lists scroll naturally inside the card. Performance: cache-hit dominates. |
| Quote name unavailable | Fall back to ticker symbol for display |
| Stale quote (cache age > 60s during market hours) | Renders normally; the small `⚠ 缓存` chip on symbol strip still triggers |

## Performance

Worst case (cold cache, 10 watchlist items):
- 10× `get_quote()` — Tencent fast path, ~50-200ms each, can run sequentially
- 10× `get_history(period="30d")` — Tencent fast path, similar
- Total: ~2-5 seconds for cold cache

Subsequent loads within 60s: all cache hits, ~100ms total.

This is acceptable. If it becomes painful, future optimization: parallel fetch via `asyncio.gather` (currently routes are sync; would need refactor).

## Tests

`tests/web/test_stock.py` — extend:

1. `test_stock_page_renders_watchlist_sidebar` — POST a watchlist item, GET /stock/AAPL, assert watchlist HTML present with ticker + price
2. `test_stock_page_watchlist_empty_state` — empty watchlist, assert "还没添加自选股" present
3. `test_stock_page_watchlist_marks_active_ticker` — current ticker in watchlist gets `is-active` class
4. `test_stock_page_uses_mp_card_classes` — body contains `.mp-card`, `.mp-chip`, `.mp-table`, `.mp-eyebrow`, `.mp-btn` somewhere
5. `test_stock_page_uses_material_symbols` — body contains `material-symbols-outlined` (icons replaced emoji)
6. `test_stock_page_loads_chart_theme_js` — `<script src="/static/chart-theme.js">` present BEFORE chart.js

## Risk

**Medium.** Significant template rewrite + new backend route logic + chart.js refactor. Mitigations:
- Chart element IDs unchanged → chart.js can still find DOM
- Watchlist failures degrade gracefully (no quote crashes the page)
- Cache-busting (PR #32) ensures users see new CSS without manual refresh
- All tests must pass before merge

Possible follow-ups noted in spec as out-of-scope:
- Watchlist categories (美股/A股/ETF) — needs DB column
- Drag-reorder watchlist
- Sparkline cache (currently relies on history fetch cache)
- HTMX-based ticker switch without full page reload

## Principles Compliance

Per `docs/PRINCIPLES.md`:

- **#1 Measure, don't auto-modify**: All visual choices come from the mockup (user-approved). No silent data changes. ✓
- **#5 Determinism**: Sparkline rendering is a pure function of values + dimensions. ✓

Other principles not relevant to this scope.

## Out of Scope

- Watchlist categories / tabs
- Watchlist drag-reorder
- Mobile responsive layout (<768px)
- Dark mode toggle
- Ticker tape / command line / F-keys (Variant B-only features)
- Side rail (record trade with type-aware fields per Variant C)
