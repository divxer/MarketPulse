# Phase 5b-2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Apply full NineScrolls Variant A visual to `/stock/{ticker}` + wire real watchlist data + port chart theme.

**Spec:** [`docs/superpowers/specs/2026-05-15-phase-5b-2-nine-scrolls-variant-a.md`](../specs/2026-05-15-phase-5b-2-nine-scrolls-variant-a.md)

**Branch:** `feat/phase-5b-2-nine-scrolls-variant-a` (spec already committed there)

---

## Pre-flight

- [ ] **Step 0a:** confirm state

```bash
git branch --show-current      # expect: feat/phase-5b-2-nine-scrolls-variant-a
git status --short             # expect: empty (spec already committed)
uv run pytest 2>&1 | tail -3   # expect: 356+ passed
```

---

## Task 1: Backend — watchlist fetch + Jinja sparkpoints filter

**Files:** `marketpulse/web/routes/stock.py`, `marketpulse/web/main.py`, `tests/web/test_sparkpoints.py` (new)

### Step 1a: Add `_sparkpoints` Jinja filter to `marketpulse/web/main.py`

Find the existing `_render_markdown` definition near the top of `main.py`. AFTER it (and after the line `templates.env.filters["markdown"] = _render_markdown`), add:

```python
def _sparkpoints(values: list[float] | None, width: int, height: int) -> str:
    """Convert a values list to SVG polyline points attribute.

    Linearly normalizes values to fit [0, width] × [0, height], inverting Y
    (higher value → smaller y, closer to top). Returns empty string for
    inputs < 2 points (no line to draw).

    Used by stock.html watchlist sparkline rendering.
    """
    if not values or len(values) < 2:
        return ""
    lo = min(values)
    hi = max(values)
    n = len(values)
    if hi == lo:
        # Flat line — horizontal at midline so it's visible
        mid = height / 2
        return " ".join(
            f"{i * width / (n - 1):.1f},{mid:.1f}" for i in range(n)
        )
    span = hi - lo
    pts = []
    for i, v in enumerate(values):
        x = i * width / (n - 1)
        y = height - (v - lo) / span * height
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


templates.env.filters["sparkpoints"] = _sparkpoints
```

### Step 1b: Create `tests/web/test_sparkpoints.py`

```python
"""Unit tests for the _sparkpoints Jinja filter."""
from marketpulse.web.main import _sparkpoints


def test_empty_returns_empty_string():
    assert _sparkpoints([], 56, 20) == ""
    assert _sparkpoints(None, 56, 20) == ""


def test_single_value_returns_empty_string():
    """Need at least 2 points to draw a line."""
    assert _sparkpoints([5.0], 56, 20) == ""


def test_two_values_render_correct_endpoints():
    """[1.0, 10.0] with (56, 20): first at (0, 20), last at (56, 0)."""
    points = _sparkpoints([1.0, 10.0], 56, 20)
    parts = points.split()
    assert len(parts) == 2
    x0, y0 = map(float, parts[0].split(","))
    x1, y1 = map(float, parts[1].split(","))
    assert x0 == 0.0
    assert y0 == 20.0  # min value → bottom
    assert x1 == 56.0
    assert y1 == 0.0   # max value → top


def test_flat_line_renders_horizontal_midline():
    """All-equal values → horizontal line at height/2."""
    points = _sparkpoints([5.0, 5.0, 5.0], 56, 20)
    parts = points.split()
    assert len(parts) == 3
    for part in parts:
        _, y = map(float, part.split(","))
        assert y == 10.0  # height/2


def test_normalizes_to_dimensions():
    """4 evenly-spaced x values; y inverted (higher value → smaller y)."""
    points = _sparkpoints([1.0, 2.0, 3.0, 4.0], 60, 30)
    parts = points.split()
    assert len(parts) == 4
    xs = [float(p.split(",")[0]) for p in parts]
    ys = [float(p.split(",")[1]) for p in parts]
    # Evenly spaced x: 0, 20, 40, 60
    assert xs == [0.0, 20.0, 40.0, 60.0]
    # Y inverted: first (lowest value) → height=30, last (highest) → 0
    assert ys[0] == 30.0
    assert ys[-1] == 0.0
    # Strictly decreasing (since values strictly increasing)
    assert ys[0] > ys[1] > ys[2] > ys[3]


def test_handles_negative_values():
    """Negative values should normalize correctly."""
    points = _sparkpoints([-2.0, 0.0, 2.0], 56, 20)
    parts = points.split()
    assert len(parts) == 3
    ys = [float(p.split(",")[1]) for p in parts]
    # -2 is min → y=20; 2 is max → y=0; 0 is midway → y=10
    assert ys[0] == 20.0
    assert ys[1] == 10.0
    assert ys[2] == 0.0
```

### Step 1c: Run the filter tests

```bash
uv run pytest tests/web/test_sparkpoints.py -v
```

Expected: 6 PASS.

### Step 1d: Update `marketpulse/web/routes/stock.py::stock_page`

Find the `stock_page` function. Find this section near the top (or wherever the holding fetch is):

```python
holding = db.query(Holding).filter(Holding.ticker == ticker).one_or_none()
in_watchlist = db.query(WatchlistItem).filter(
    WatchlistItem.ticker == ticker,
).one_or_none() is not None
```

Insert BEFORE the `holding =` line (so watchlist fetch runs early enough to be in the render context):

```python
    # Watchlist sidebar data (Phase 5b-2). Capped to bound worst-case
    # cold-cache page time. User can add more but the rail only shows
    # the first N.
    MAX_WATCHLIST_RENDER = 20
    watchlist_rows = (
        db.query(WatchlistItem.ticker)
        .order_by(WatchlistItem.created_at.asc())
        .limit(MAX_WATCHLIST_RENDER)
        .all()
    )
    watchlist_items: list[dict] = []
    for (wl_ticker,) in watchlist_rows:
        try:
            wl_quote = data.get_quote(wl_ticker)
            try:
                wl_bars = data.get_history(wl_ticker, period="30d")
                spark = [b.close for b in wl_bars[-30:]]
            except Exception:  # noqa: BLE001
                spark = []
            watchlist_items.append({
                "ticker": wl_ticker,
                "name": getattr(wl_quote, "name", None) or wl_ticker,
                "price": wl_quote.price,
                "change_pct": wl_quote.change_pct,
                "sparkline": spark,
                "is_active": wl_ticker == ticker,
            })
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "stock_page_watchlist_quote_failed",
                ticker=wl_ticker, error=str(exc),
            )
            watchlist_items.append({
                "ticker": wl_ticker,
                "name": wl_ticker,
                "price": None,
                "change_pct": None,
                "sparkline": [],
                "is_active": wl_ticker == ticker,
            })
```

Then in the `templates.TemplateResponse(...)` call near the end of `stock_page`, add `"watchlist_items": watchlist_items` to the context dict:

```python
    return templates.TemplateResponse(
        request, "stock.html",
        {
            "ticker": ticker,
            "quote": quote,
            "bars": bars,
            "news": news,
            "holding": holding,
            "in_watchlist": in_watchlist,
            "recent_trades": recent_trades,
            "watchlist_items": watchlist_items,  # NEW
        },
    )
```

### Step 1e: Run tests

```bash
uv run pytest tests/web/test_stock.py tests/web/test_sparkpoints.py -v 2>&1 | tail -15
uv run pytest 2>&1 | tail -3
uv run ruff check 2>&1 | tail -3
```

All green expected.

### Step 1f: Commit

```bash
git add marketpulse/web/main.py marketpulse/web/routes/stock.py tests/web/test_sparkpoints.py
git commit -m "$(cat <<'EOF'
feat(stock): backend watchlist fetch + sparkpoints Jinja filter

Pulls first MAX_WATCHLIST_RENDER (20) watchlist tickers, fetches quote
+ 30d history for each (sparkline), and passes as `watchlist_items` to
stock.html. Failures degrade gracefully — ticker still rendered with
"数据不可用" caption.

New _sparkpoints Jinja filter normalizes a values list to an SVG
polyline points attribute. Returns empty string for <2 points;
flat-line case renders horizontal midline; Y is inverted (higher
value → smaller y).

6 unit tests cover empty / single / 2-point / flat / N-point / negative
cases.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: chart-theme.js (new file)

**Files:** `marketpulse/web/static/chart-theme.js` (new)

### Step 2a: Create the file

```javascript
// MarketPulse — chart color tokens (Variant A · NineScrolls light theme).
// Mirrors the SVG mockup palette (chart-svg.jsx > themeColors()).
// Production chart.js consumes these via window.MP_CHART_THEME.

window.MP_CHART_THEME = {
  // Background and grid
  background: "#ffffff",
  textColor: "#022448",            // ns-navy
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
  ema12: "#0066cc",                // ns-primary
  ema26: "#f59e0b",                // amber (kept for contrast)
  sma50: "#8b5cf6",                // purple
  sma200: "#022448",               // navy (long-term trend gets the navy)
  bbUpper: "#a855f7",              // purple, dashed
  bbLower: "#a855f7",

  // RSI
  rsiLine: "#0066cc",              // ns-primary
  rsiOverbought: "#fca5a5",
  rsiOversold: "#93c5fd",

  // MACD
  macdLine: "#0066cc",             // ns-primary
  macdSignal: "#f59e0b",           // amber
  macdHistPositive: "rgba(14,138,95,0.6)",
  macdHistNegative: "rgba(192,57,43,0.6)",

  // Signal markers
  signalGoldenCross:   "#16a34a",  // green
  signalDeathCross:    "#dc2626",  // red
  signalOverbought:    "#f59e0b",  // amber
  signalOversold:      "#3b82f6",  // blue
  signalBollingerUpper: "#a855f7",
  signalBollingerLower: "#6366f1",
};
```

### Step 2b: Sanity check the file is served

```bash
ls -la marketpulse/web/static/chart-theme.js
# size should be ~1.5KB
```

### Step 2c: Commit

```bash
git add marketpulse/web/static/chart-theme.js
git commit -m "$(cat <<'EOF'
feat(chart): chart-theme.js — color tokens for NineScrolls Variant A

Extracts the SVG mockup's themeColors() into a window.MP_CHART_THEME
constant that production chart.js consumes. Replaces hardcoded hex
values with token-driven palette aligned to ns-tokens.css (navy,
primary blue, mp-up green, mp-down red).

Loaded BEFORE chart.js in stock.html so the constant is available at
chart initialization.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: chart.js refactor to use theme constants

**Files:** `marketpulse/web/static/chart.js`

### Step 3a: Audit current hardcoded colors

```bash
grep -nE 'color: ?"#|color:\s*"#|wick(Up|Down)Color|upColor|downColor|borderColor' marketpulse/web/static/chart.js | head -30
```

You'll find candle, line series, and histogram colors scattered through `renderCharts`.

### Step 3b: Replace each color literal with `window.MP_CHART_THEME.*`

For the candle series creation (find `addCandlestickSeries`), the typical block is:

```javascript
    s.candleSeries = s.mainChart.addCandlestickSeries({
      upColor: "#16a34a", downColor: "#dc2626",
      borderVisible: false, wickUpColor: "#16a34a", wickDownColor: "#dc2626",
    });
```

Replace with:

```javascript
    const T = window.MP_CHART_THEME;
    s.candleSeries = s.mainChart.addCandlestickSeries({
      upColor: T.upColor, downColor: T.downColor,
      borderVisible: false,
      wickUpColor: T.wickUpColor, wickDownColor: T.wickDownColor,
    });
```

Do the same for EVERY hardcoded color in chart.js. Match the mapping:

| Find (in chart.js) | Replace with |
|---|---|
| `"#16a34a"` (candle up) | `T.upColor` |
| `"#dc2626"` (candle down) | `T.downColor` |
| `"#0ea5e9"` (EMA12) | `T.ema12` |
| `"#f59e0b"` (EMA26 line) | `T.ema26` |
| `"#8b5cf6"` (SMA50) | `T.sma50` |
| `"#64748b"` (SMA200 was slate, now navy) | `T.sma200` |
| `"#a855f7"` (BB upper/lower) | `T.bbUpper` / `T.bbLower` |
| `"#9333ea"` (RSI line) | `T.rsiLine` |
| `"#fca5a5"` (RSI overbought) | `T.rsiOverbought` |
| `"#93c5fd"` (RSI oversold) | `T.rsiOversold` |
| `"#0ea5e9"` (MACD line — same hex as EMA12 was, but semantic-different) | `T.macdLine` |
| `"#f59e0b"` (MACD signal) | `T.macdSignal` |
| `"rgba(22,163,74,0.6)"` (MACD hist positive) | `T.macdHistPositive` |
| `"rgba(220,38,38,0.6)"` (MACD hist negative) | `T.macdHistNegative` |
| `SIGNAL_STYLES.ema_golden_cross.color: "#16a34a"` | `T.signalGoldenCross` |
| `SIGNAL_STYLES.ema_death_cross.color: "#dc2626"` | `T.signalDeathCross` |
| `SIGNAL_STYLES.rsi_overbought.color: "#f59e0b"` | `T.signalOverbought` |
| `SIGNAL_STYLES.rsi_oversold.color: "#3b82f6"` | `T.signalOversold` |
| `SIGNAL_STYLES.bollinger_upper.color: "#a855f7"` | `T.signalBollingerUpper` |
| `SIGNAL_STYLES.bollinger_lower.color: "#6366f1"` | `T.signalBollingerLower` |

For `SIGNAL_STYLES` constant near the top of chart.js — define `T` early:

```javascript
(function () {
  const T = window.MP_CHART_THEME || {};
  const SIGNAL_STYLES = {
    ema_golden_cross:  { shape: "arrowUp",   color: T.signalGoldenCross   || "#16a34a", text: "金叉" },
    ema_death_cross:   { shape: "arrowDown", color: T.signalDeathCross    || "#dc2626", text: "死叉" },
    rsi_overbought:    { shape: "circle",    color: T.signalOverbought    || "#f59e0b", text: "超买" },
    rsi_oversold:      { shape: "circle",    color: T.signalOversold      || "#3b82f6", text: "超卖" },
    bollinger_upper:   { shape: "square",    color: T.signalBollingerUpper || "#a855f7", text: "上轨" },
    bollinger_lower:   { shape: "square",    color: T.signalBollingerLower || "#6366f1", text: "下轨" },
  };
  // ... rest of IIFE
```

Note: keep the fallback `|| "#xxxxxx"` so a missing chart-theme.js doesn't crash chart.js. Graceful degradation.

Also update the chart common options to use theme:

```javascript
const commonOpts = {
  autoSize: true,
  layout: { background: { color: T.background || "#ffffff" }, textColor: T.textColor || "#334155" },
  grid: { vertLines: { color: T.gridLines || "#e2e8f0" }, horzLines: { color: T.gridLines || "#e2e8f0" } },
  timeScale: { borderColor: T.borderColor || "#cbd5e1", rightOffset: 12 },
  crosshair: { mode: 0 },
};
```

### Step 3c: Verify no syntax errors

```bash
node --check marketpulse/web/static/chart.js
```

Expected: no output (success). If node isn't available, skip — backend tests below cover the route, manual deploy test covers chart render.

### Step 3d: Run tests

```bash
uv run pytest 2>&1 | tail -3
```

All green (chart.js isn't directly tested by Python tests, but no test should break).

### Step 3e: Commit

```bash
git add marketpulse/web/static/chart.js
git commit -m "$(cat <<'EOF'
refactor(chart): consume MP_CHART_THEME constants instead of hex literals

All previously-hardcoded color hex values in chart.js (candle, EMA,
SMA, BB, RSI, MACD lines + histograms + signal markers + grid/text/
border) now read from window.MP_CHART_THEME. Fallback to original
hex on missing theme so chart.js works without chart-theme.js.

Visual changes vs before:
- EMA12: light blue → primary blue (#0066cc)
- SMA200: slate gray → navy (#022448)
- RSI line: purple → primary blue
- MACD line: light blue → primary blue

All other colors preserved.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: app.css — add `.mp-watchlist*` classes

**Files:** `marketpulse/web/static/css/app.css`

### Step 4a: Append `.mp-watchlist*` rules

At the END of `marketpulse/web/static/css/app.css`, add (use NineScrolls tokens from `ns-tokens.css`):

```css
/* ============ Watchlist sidebar (Variant A left rail) ============ */

.mp-watchlist {
  list-style: none;
  margin: 0;
  padding: 0;
}

.mp-watchlist__item {
  border-bottom: 1px solid var(--ns-outline-variant);
  border-left: 3px solid transparent;
}

.mp-watchlist__item.is-active {
  background: var(--ns-surface-container);
  border-left-color: var(--ns-primary);
}

.mp-watchlist__link {
  display: block;
  padding: 10px 14px;
  color: inherit;
  text-decoration: none;
}

.mp-watchlist__link:hover {
  background: var(--ns-surface-container-low);
}

.mp-watchlist__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.mp-watchlist__row--values {
  margin-top: 4px;
}

.mp-watchlist__id {
  min-width: 0;          /* allows ellipsis to kick in */
  flex: 1 1 auto;
}

.mp-watchlist__ticker {
  font-weight: 700;
  font-size: 13px;
  color: var(--ns-navy);
  letter-spacing: -0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mp-watchlist__name {
  font-size: 10.5px;
  color: var(--ns-on-surface-variant);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mp-watchlist__spark {
  flex-shrink: 0;
}

.mp-watchlist__price {
  font-size: 13px;
  font-weight: 600;
  color: var(--ns-navy);
}

.mp-watchlist__chg--up   { color: var(--mp-up);   font-size: 11.5px; font-weight: 600; }
.mp-watchlist__chg--down { color: var(--mp-down); font-size: 11.5px; font-weight: 600; }
```

### Step 4b: Rebuild Tailwind (doesn't affect new app.css but ensures consistency)

```bash
npm run build:css 2>&1 | tail -3
```

### Step 4c: Verify

```bash
grep -c "mp-watchlist" marketpulse/web/static/css/app.css
```
Expect: 13+ matches.

### Step 4d: Commit

```bash
git add marketpulse/web/static/css/app.css
git commit -m "$(cat <<'EOF'
feat(css): add .mp-watchlist* classes for Variant A left-rail sidebar

13 new selectors covering the watchlist card structure:
- .mp-watchlist (ul reset)
- .mp-watchlist__item (li with bottom-border + left-border placeholder)
- .mp-watchlist__item.is-active (navy left-border, surface fill)
- .mp-watchlist__link (a, full-row click target)
- .mp-watchlist__row (flex layout)
- .mp-watchlist__id / __ticker / __name (text-overflow ellipsis)
- .mp-watchlist__spark (svg, flex-shrink-0)
- .mp-watchlist__price / __chg--up / __chg--down (tabular nums tints)

Long ticker/name handled via text-overflow: ellipsis + white-space:
nowrap + min-width: 0 on the flex parent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: stock.html — full visual rewrite

**Files:** `marketpulse/web/templates/stock.html`

This is the biggest task. Read the existing stock.html first, then make targeted edits.

### Step 5a: Read current stock.html to anchor the rewrites

```bash
wc -l marketpulse/web/templates/stock.html
sed -n '1,80p' marketpulse/web/templates/stock.html
sed -n '80,200p' marketpulse/web/templates/stock.html
```

### Step 5b: Add `chart-theme.js` script tag BEFORE `chart.js`

Find the existing:

```html
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.2/dist/lightweight-charts.standalone.production.js"></script>
<script src="/static/chart.js"></script>
```

Replace with:

```html
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.2/dist/lightweight-charts.standalone.production.js"></script>
<script src="/static/chart-theme.js?v={{ static_version('chart-theme.js') }}"></script>
<script src="/static/chart.js?v={{ static_version('chart.js') }}"></script>
```

(Note: also apply cache-busting to chart.js while we're at it.)

### Step 5c: Replace the LEFT rail (watchlist) with real data

Find this current block (the placeholder):

```html
    <!-- ============ LEFT: watchlist placeholder ============ -->
    <aside class="bg-slate-50 rounded p-4 text-sm text-slate-500">
      <div class="font-semibold text-slate-700 mb-2">自选股</div>
      <div class="text-xs">侧栏数据 — Phase 5b-2</div>
    </aside>
```

Replace with:

```html
    <!-- ============ LEFT: watchlist sidebar ============ -->
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
                          fill="none"
                          stroke="{% if item.change_pct is not none and item.change_pct >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %}"
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
        <li style="padding: 16px 14px; font-size: 12px; color: var(--ns-on-surface-variant); text-align: center;">
          还没添加自选股 — 在任意股票页面点 ☆
        </li>
        {% endfor %}
      </ul>
    </aside>
```

### Step 5d: Replace the symbol strip header

Find the current header:

```html
  <!-- ============ SYMBOL STRIP (full width) ============ -->
  <header class="flex flex-wrap items-baseline justify-between gap-3">
    <div class="flex items-baseline gap-3 flex-wrap">
      <h1 class="text-xl font-semibold">{{ ticker }}</h1>
      <span class="text-2xl font-semibold">${{ "%.2f"|format(quote.price) }}</span>
      <span class="text-sm {{ change_color }}">
        {{ "%+.2f"|format(change_amount) }} ({{ "%+.2f"|format(quote.change_pct) }}%)
      </span>
      {% if quote.stale %}<span class="text-amber-600 text-xs">⚠ 缓存</span>{% endif %}
    </div>
    <div class="flex items-center gap-2 text-xs">
      <form hx-post="/watchlist" hx-target="this" hx-swap="outerHTML" class="inline">
        <input type="hidden" name="ticker" value="{{ ticker }}" />
        <button class="px-2 py-1 rounded border border-slate-200 hover:bg-slate-50"
                {% if in_watchlist %}disabled title="已在自选股"{% endif %}>
          {% if in_watchlist %}★ 已自选{% else %}☆ 加自选{% endif %}
        </button>
      </form>
      <button hx-post="/stock/{{ ticker }}/analyze" hx-target="#analysis" hx-swap="innerHTML"
              class="bg-slate-900 text-white px-3 py-1 rounded">🤖 AI 分析</button>
    </div>
  </header>
```

Replace with:

```html
  <!-- ============ SYMBOL STRIP (full width) ============ -->
  <header class="flex flex-wrap items-baseline justify-between gap-3 mp-symbol-strip">
    <div class="flex items-baseline gap-3 flex-wrap">
      <h1 class="grotesk" style="font-size: 32px; font-weight: 700; letter-spacing: -0.03em; color: var(--ns-navy); margin: 0;">{{ ticker }}</h1>
      <span class="mono tnum" style="font-size: 32px; font-weight: 600; letter-spacing: -0.02em; color: var(--ns-navy);">${{ "%.2f"|format(quote.price) }}</span>
      <span class="mono tnum grotesk" style="font-size: 16px; font-weight: 700; color: {% if quote.change_pct >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};">
        <span class="material-symbols-outlined" style="font-size: 16px; vertical-align: middle;">{% if quote.change_pct >= 0 %}trending_up{% else %}trending_down{% endif %}</span>
        {{ "%+.2f"|format(change_amount) }} ({{ "%+.2f"|format(quote.change_pct) }}%)
      </span>
      {% if quote.stale %}
      <span class="mp-chip" style="background: #fef3c7; color: #92400e; padding: 2px 8px; font-size: 11px;">
        <span class="material-symbols-outlined" style="font-size: 12px;">warning</span> 缓存
      </span>
      {% endif %}
    </div>
    <div class="flex items-center gap-2 text-xs">
      <form hx-post="/watchlist" hx-target="this" hx-swap="outerHTML" class="inline">
        <input type="hidden" name="ticker" value="{{ ticker }}" />
        <button class="mp-btn mp-btn--ghost"
                {% if in_watchlist %}disabled title="已在自选股"{% endif %}>
          <span class="material-symbols-outlined">{% if in_watchlist %}star{% else %}star_border{% endif %}</span>
          {% if in_watchlist %}已自选{% else %}加自选{% endif %}
        </button>
      </form>
      <button hx-post="/stock/{{ ticker }}/analyze" hx-target="#analysis" hx-swap="innerHTML"
              class="mp-btn mp-btn--navy">
        <span class="material-symbols-outlined">auto_awesome</span>
        AI 分析
      </button>
    </div>
  </header>
  <div class="mp-rule"></div>
```

### Step 5e: Replace position card and record-trade form in the RIGHT rail

Find this block (inside the `<aside>` right rail):

```html
      <!-- Position card (if holding) -->
      {% if holding %}
      ...
      <div class="px-3 py-2 bg-slate-50 rounded text-sm">
        <div class="font-semibold text-slate-700 mb-2">当前持仓</div>
        ...
      </div>
      {% endif %}

      <!-- Record-trade form (always visible now) -->
      <form id="record-trade-form" class="px-3 py-2 bg-amber-50 rounded text-sm"
            hx-post="/trades" ...>
        <div class="font-semibold text-slate-700 mb-2">📝 记一笔</div>
        ...
      </form>

      <!-- AI analysis -->
      <div id="analysis"></div>

      <!-- News list -->
      <div class="px-3 py-2 bg-slate-50 rounded text-sm">
        <h2 class="font-semibold text-slate-700 mb-2">最新新闻</h2>
        ...
      </div>
```

Replace with NineScrolls cards:

```html
      <!-- Position card (if holding) -->
      {% if holding %}
      {% set market_value = holding.quantity * quote.price %}
      {% set cost = holding.quantity * holding.avg_cost %}
      {% set pl_dollars = market_value - cost %}
      {% set pl_pct = (pl_dollars / cost * 100) if cost else 0 %}
      <section class="mp-card">
        <div class="mp-card__head">
          <span class="mp-card__title">
            <span class="material-symbols-outlined">account_balance_wallet</span>
            当前持仓
          </span>
        </div>
        <div class="mp-card__body">
          <div class="flex flex-wrap gap-x-4 gap-y-1 items-baseline text-sm">
            <span><span class="mono tnum" style="font-weight: 600;">{{ "%g"|format(holding.quantity) }}</span> 股</span>
            <span style="color: var(--ns-on-surface-variant);">@ 均价</span>
            <span class="mono tnum">${{ "%.2f"|format(holding.avg_cost) }}</span>
            <span style="color: var(--ns-on-surface-variant);">市值</span>
            <span class="mono tnum">${{ "%.2f"|format(market_value) }}</span>
          </div>
          <div class="mt-2 mono tnum" style="font-weight: 600; color: {% if pl_dollars >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};">
            {{ "%+.2f"|format(pl_dollars) }} ({{ "%+.2f"|format(pl_pct) }}%)
          </div>
        </div>
      </section>
      {% endif %}

      <!-- Record-trade form -->
      <section class="mp-card">
        <div class="mp-card__head">
          <span class="mp-card__title">
            <span class="material-symbols-outlined">edit_note</span>
            记一笔
          </span>
        </div>
        <div class="mp-card__body">
          <form id="record-trade-form"
                hx-post="/trades" hx-target="#record-trade-msg" hx-swap="innerHTML"
                hx-on::after-request="if(event.detail.successful) { this.querySelector('[name=quantity]').value=''; }">
            <input type="hidden" name="ticker" value="{{ ticker }}" />
            <div class="flex flex-wrap gap-2 items-center">
              <select name="action" class="border rounded px-2 py-1 text-sm">
                <option value="buy">买入</option>
                <option value="sell">卖出</option>
              </select>
              <input name="quantity" type="number" step="any" min="0" placeholder="数量" required
                     class="border rounded px-2 py-1 w-24 text-sm" />
              <input name="price" type="number" step="any" min="0" placeholder="价格 $" required
                     value="{{ "%.2f"|format(quote.price) }}" class="border rounded px-2 py-1 w-24 text-sm" />
              <input name="executed_at" type="date" class="border rounded px-2 py-1 text-sm" />
              <button class="mp-btn mp-btn--navy">提交</button>
            </div>
            <span id="record-trade-msg" class="text-xs block mt-2" style="color: var(--ns-on-surface-variant);"></span>
          </form>
        </div>
      </section>

      <!-- AI analysis -->
      <div id="analysis"></div>

      <!-- News list -->
      <section class="mp-card">
        <div class="mp-card__head">
          <span class="mp-card__title">
            <span class="material-symbols-outlined">article</span>
            最新新闻
          </span>
        </div>
        <div class="mp-card__body">
          <ul class="space-y-1 text-sm">
            {% for n in news %}
            <li>
              <a href="{{ n.url }}" style="color: var(--ns-primary);">{{ n.headline }}</a>
              <span class="text-xs" style="color: var(--ns-on-surface-variant);">— {{ n.source }}</span>
            </li>
            {% else %}
            <li class="text-xs" style="color: var(--ns-on-surface-variant);">暂无新闻(yfinance 限流时常见,几小时后会恢复)</li>
            {% endfor %}
          </ul>
        </div>
      </section>
```

### Step 5f: Replace period buttons + recent trades table (CENTER column)

Find period buttons:

```html
      <!-- Period buttons + indicator toggles -->
      <div class="flex flex-wrap gap-1 text-xs items-center">
        <button data-period="60d" class="px-2 py-1 rounded border border-slate-200">60D</button>
        ...
```

Replace with `.mp-seg`:

```html
      <!-- Period buttons + indicator toggles -->
      <div class="flex flex-wrap gap-2 text-xs items-center">
        <div class="mp-seg">
          <button data-period="60d">60D</button>
          <button data-period="6m">6M</button>
          <button data-period="ytd">YTD</button>
          <button data-period="1y">1Y</button>
          <button data-period="5y">5Y</button>
          <button data-period="all">All</button>
        </div>
        <span class="ml-3 flex items-center gap-2" style="color: var(--ns-on-surface-variant);">
          <label class="cursor-pointer"><input type="checkbox" id="toggle-bb" checked /> 布林带</label>
          <label class="cursor-pointer"><input type="checkbox" id="toggle-sma" checked /> SMA50/200</label>
        </span>
      </div>
```

Find recent trades table:

```html
      {% if recent_trades %}
      <h2 class="mt-6 font-semibold flex items-center gap-2 text-sm">
        最近交易 <a href="/trades?ticker={{ ticker }}" class="text-slate-500 text-xs hover:underline">全部 →</a>
      </h2>
      <table class="mt-2 text-sm w-full">
```

Replace with `.mp-card` containing `.mp-table`:

```html
      {% if recent_trades %}
      <section class="mp-card mt-6">
        <div class="mp-card__head">
          <span class="mp-card__title">
            <span class="material-symbols-outlined">history</span>
            最近交易
          </span>
          <a href="/trades?ticker={{ ticker }}" class="text-xs" style="color: var(--ns-primary);">全部 →</a>
        </div>
        <table class="mp-table" style="width: 100%;">
          <tbody>
          {% for t in recent_trades %}
            <tr>
              <td class="mono" style="color: var(--ns-on-surface-variant); font-size: 11px; width: 7rem;">
                {{ (t.executed_at or t.created_at).strftime("%Y-%m-%d") }}
              </td>
              <td style="width: 4rem;">
                {% if t.action == "buy" %}<span class="mp-chip mp-chip--periwinkle">买入</span>
                {% else %}<span class="mp-chip mp-chip--down">卖出</span>{% endif %}
              </td>
              <td class="mono tnum" style="text-align: right; width: 5rem;">{{ "%g"|format(t.quantity) }}</td>
              <td class="mono tnum" style="text-align: right; width: 6rem;">${{ "%.2f"|format(t.price) }}</td>
              <td class="mono tnum text-xs" style="text-align: right; color: {% if t.realized_pl is not none and t.realized_pl >= 0 %}var(--mp-up){% elif t.realized_pl is not none %}var(--mp-down){% endif %};">
                {% if t.realized_pl is not none %}{{ "%+.2f"|format(t.realized_pl) }}{% endif %}
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </section>
      {% endif %}
```

### Step 5g: Replace section container

Find the outer `<section class="bg-white rounded-md shadow-sm p-4">` wrapping the whole page. Keep it (it's still a fine outer container) but the inside is now full NineScrolls.

### Step 5h: Run tests + manual sanity

```bash
uv run pytest 2>&1 | tail -3
uv run ruff check 2>&1 | tail -3
npm run build:css 2>&1 | tail -3
```

All green expected. The Tailwind rebuild captures any new utility classes used in the rewritten template.

### Step 5i: Commit

```bash
git add marketpulse/web/templates/stock.html marketpulse/web/static/app.css
git commit -m "$(cat <<'EOF'
feat(stock): Phase 5b-2 — full NineScrolls Variant A visual

stock.html rewritten to apply NineScrolls design language across the
3-column layout from Phase 5b-1:

Symbol strip:
- Space Grotesk for ticker h1 (32px, navy, -0.03em tracking)
- Mono + tabular nums for price (32px, navy)
- Material Symbols for trending_up/trending_down + warning
- mp-btn--navy for AI 分析 action; mp-btn--ghost for star/watchlist
- mp-rule 80×4 px under the strip (signature NineScrolls section break)

Left rail watchlist:
- Real data (watchlist_items context from route)
- mp-card with mp-card__head + mp-watchlist__* list items
- Sparkline rendered as inline SVG polyline via _sparkpoints filter
- Active ticker gets primary-blue left border + surface-container fill
- Empty state copy when no items
- Graceful "数据不可用" when quote fetch failed

Center column:
- mp-seg segmented control for period buttons (was loose buttons)
- mp-card wrapping recent trades; mp-table with mp-chip badges for buy/sell

Right rail:
- mp-card for position info (account_balance_wallet icon)
- mp-card for record-trade form (edit_note icon, navy submit)
- mp-card for news (article icon)
- All hex colors replaced with var(--ns-*) and var(--mp-*) tokens

Chart theme: chart-theme.js loaded before chart.js. EMA12 / SMA200 /
RSI / MACD lines re-themed to primary blue + navy where appropriate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Visual + watchlist tests

**Files:** `tests/web/test_stock.py`

### Step 6a: Append new tests

```python
def test_stock_page_renders_watchlist_sidebar(client: TestClient, monkeypatch):
    """When user has watchlist items, /stock/{ticker} shows them in left rail."""
    _login(client, monkeypatch)
    # Add a few watchlist items
    client.post("/watchlist", data={"ticker": "AAPL"})
    client.post("/watchlist", data={"ticker": "MSFT"})

    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        r = client.get("/stock/AAPL")
        assert r.status_code == 200
        body = r.text
        assert "mp-watchlist" in body
        assert "MSFT" in body  # other ticker present
        # Active marker on current ticker — both should be in DOM but only AAPL active
        assert "is-active" in body
    finally:
        client.app.dependency_overrides.clear()


def test_stock_page_watchlist_empty_state(client: TestClient, monkeypatch):
    """No watchlist items → empty state copy renders."""
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        r = client.get("/stock/AAPL")
        assert r.status_code == 200
        assert "还没添加自选股" in r.text
    finally:
        client.app.dependency_overrides.clear()


def test_stock_page_watchlist_caps_at_max_render(client: TestClient, monkeypatch):
    """Adding > MAX_WATCHLIST_RENDER items renders only first MAX in sidebar."""
    _login(client, monkeypatch)
    # Add 25 watchlist items
    for i in range(25):
        client.post("/watchlist", data={"ticker": f"TST{i:02d}"})

    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        r = client.get("/stock/TST00")
        body = r.text
        # First 20 should be present, items 20-24 should not
        for i in range(20):
            assert f"TST{i:02d}" in body, f"TST{i:02d} should appear in first 20"
        for i in range(20, 25):
            # Crude check — the ticker shouldn't appear in the watchlist HTML.
            # We just verify it's not in the body at all (these tickers don't
            # appear elsewhere in the page since they're not the current ticker
            # of /stock/TST00 — only TST00 is active).
            # Since the loop adds 25 items including TST00, items 20-24 are
            # beyond the cap. Their tickers should not appear in the rail.
            # (TST00 does appear because it's the active ticker in the header.)
            assert f"TST{i:02d}" not in body, (
                f"TST{i:02d} should be beyond MAX_WATCHLIST_RENDER cap"
            )
    finally:
        client.app.dependency_overrides.clear()


def test_stock_page_uses_mp_card_classes(client: TestClient, monkeypatch):
    """Body contains NineScrolls component classes."""
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        r = client.get("/stock/AAPL")
        body = r.text
        assert "mp-card" in body
        assert "mp-card__head" in body
        assert "mp-seg" in body
        assert "mp-rule" in body
        # mp-btn for action buttons
        assert "mp-btn" in body
    finally:
        client.app.dependency_overrides.clear()


def test_stock_page_uses_material_symbols(client: TestClient, monkeypatch):
    """Emoji replaced with Material Symbols Outlined."""
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        r = client.get("/stock/AAPL")
        body = r.text
        assert "material-symbols-outlined" in body
        # Specific icons we expect
        assert "auto_awesome" in body  # AI 分析
        assert "edit_note" in body     # 记一笔
        assert "visibility" in body    # 自选股 (watchlist eye)
        # Emoji should be GONE from the symbol strip and action area
        # (still allowed in stale-warning fallback if quote.stale, but
        # the empty-state "在任意股票页面点 ☆" still has ☆ — that's OK)
        assert "🤖" not in body, "🤖 emoji should be replaced with Material Symbol"
        assert "📝" not in body, "📝 emoji should be replaced with Material Symbol"
    finally:
        client.app.dependency_overrides.clear()


def test_stock_page_loads_chart_theme_js_before_chart_js(client: TestClient, monkeypatch):
    """chart-theme.js must be loaded BEFORE chart.js."""
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        r = client.get("/stock/AAPL")
        body = r.text
        idx_theme = body.find("/static/chart-theme.js")
        idx_chart = body.find("/static/chart.js")
        assert idx_theme != -1, "chart-theme.js not loaded"
        assert idx_chart != -1, "chart.js not loaded"
        assert idx_theme < idx_chart, (
            "chart-theme.js must be loaded BEFORE chart.js for window.MP_CHART_THEME"
        )
    finally:
        client.app.dependency_overrides.clear()
```

The `_FakeData` class already exists at the top of `test_stock.py` — it provides `get_quote()` returning a fake Quote. For watchlist sparkline support, ensure `_FakeData.get_history()` returns something the sparkline loop can iterate; the existing implementation returns `[Bar(...)]` (1 bar) which is fine — the sparkline will be empty (need ≥2), but no crash.

### Step 6b: Run tests

```bash
uv run pytest tests/web/test_stock.py -v 2>&1 | tail -30
uv run pytest 2>&1 | tail -3
uv run ruff check 2>&1 | tail -3
```

Expected: all new tests PASS, full project all green.

### Step 6c: Commit

```bash
git add tests/web/test_stock.py
git commit -m "$(cat <<'EOF'
test(stock): Phase 5b-2 — watchlist + visual + chart-theme assertions

6 new tests verifying:
- Watchlist sidebar renders with real items
- Empty state copy when no items
- MAX_WATCHLIST_RENDER cap is enforced (test with 25 items)
- mp-card / mp-card__head / mp-seg / mp-rule / mp-btn classes present
- Material Symbols replace 🤖 / 📝 emoji
- chart-theme.js loaded BEFORE chart.js (for window.MP_CHART_THEME)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Push + open PR

### Step 7a: Push

```bash
git push -u origin feat/phase-5b-2-nine-scrolls-variant-a
```

### Step 7b: Open PR

```bash
gh pr create --title "feat(stock): Phase 5b-2 — NineScrolls Variant A full visual + watchlist data" --body "$(cat <<'EOF'
## Summary

Completes the \`/stock/{ticker}\` redesign by applying the full NineScrolls Variant A visual language + wiring real watchlist data into the left rail + porting chart theme colors. Phase 5b-1 (PR #30) shipped the 3-column skeleton; this PR makes it look like the mockup.

## What lands

1. **Watchlist sidebar** (left rail): real data from \`WatchlistItem\` table, capped at \`MAX_WATCHLIST_RENDER=20\`. Each item shows ticker + name + price + % change + 30-day sparkline (inline SVG). Active ticker gets primary-blue left border. Empty state and "数据不可用" graceful failures.

2. **NineScrolls visual classes**: \`.mp-card\`, \`.mp-chip\`, \`.mp-table\`, \`.mp-eyebrow\`, \`.mp-btn\`, \`.mp-rule\`, \`.mp-seg\` applied across the page. New \`.mp-watchlist*\` classes added to \`app.css\` (13 selectors).

3. **Typography + color tokens**: Space Grotesk for ticker h1, mono + tabular-nums for prices/percentages, navy for headings, primary blue for actions/links.

4. **Material Symbols icons**: \`auto_awesome\` (AI 分析), \`edit_note\` (记一笔), \`visibility\` (自选股), \`star\`/\`star_border\` (watchlist toggle), \`trending_up\`/\`trending_down\` (change arrow), \`warning\` (stale badge), \`account_balance_wallet\` (持仓), \`article\` (news), \`history\` (recent trades).

5. **80×4 px section rule** under the symbol strip h1.

6. **Chart theme port**: \`window.MP_CHART_THEME\` constants in new \`chart-theme.js\`; \`chart.js\` refactored to consume them. Visual changes: EMA12 → primary blue, SMA200 → navy, RSI/MACD lines → primary blue. All other colors preserved.

7. **\`_sparkpoints\` Jinja filter** with 6 unit tests covering empty / single / 2-point / flat / N-point / negative cases.

## Spec

\`docs/superpowers/specs/2026-05-15-phase-5b-2-nine-scrolls-variant-a.md\` (340 lines, user-reviewed and revised).

## Test Plan

- [x] All tests pass (~362 expected: 356 baseline + 6 sparkpoints + 6 stock-visual)
- [x] \`ruff check\` clean
- [ ] Manual after deploy (hard refresh):
  - [ ] Open /stock/AAPL: navy + Space Grotesk + primary-blue accents visible
  - [ ] Watchlist sidebar renders real tickers with sparklines + correct active state
  - [ ] Click watchlist item → navigates to /stock/{new_ticker}, becomes new active
  - [ ] Symbol strip shows Material Symbols (trending_up arrow, auto_awesome AI button)
  - [ ] 80×4 rule visible under ticker
  - [ ] Position card, record-trade form, AI analysis, news all in mp-card style
  - [ ] Recent trades table styled with .mp-table + buy/sell chips
  - [ ] Chart still works (period buttons, OHLC hover, lazy-load)
  - [ ] Chart colors: candles green/red (mp-up/mp-down), EMA12 now primary blue
  - [ ] Empty watchlist shows "还没添加自选股 — 在任意股票页面点 ☆"

## Risk

**Medium.** Significant template rewrite + backend route additions + chart.js refactor. Mitigations:
- Chart element IDs unchanged → chart.js still finds DOM
- Watchlist failures degrade gracefully
- chart.js theme constants have hex fallbacks if chart-theme.js fails to load
- Cache-busting (PR #32) ensures fresh CSS without manual refresh

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Step 7c: Self-review checklist

Run each and report:
- `git log --oneline | head -10` — 7+ commits ahead of main
- `uv run pytest 2>&1 | tail -3` — all green, ~362 tests
- `uv run ruff check 2>&1 | tail -3` — clean
- `grep -c 'mp-watchlist' marketpulse/web/static/css/app.css` — 13+
- `grep -c 'MP_CHART_THEME' marketpulse/web/static/chart.js` — at least 5
- `grep -c 'material-symbols-outlined' marketpulse/web/templates/stock.html` — at least 6
- `grep -c 'mp-card' marketpulse/web/templates/stock.html` — at least 5

Report PR URL.
