# Chart Defaults + Period Range + OHLC Bar — Design Spec

**Status:** Approved
**Author:** harvey
**Date:** 2026-05-12

## Goal

Three independent improvements to `/stock/{ticker}` chart, motivated by side-by-side comparison with tradingview.com:

1. **Default 1Y + localStorage memory** — open chart shows 1 year, not 60 days. Persist last-chosen period across sessions.
2. **More period buttons (YTD / 5Y / All)** — match TradingView's `1M 3M 6M YTD 1Y 5Y All` granularity. Drop 30D (low value next to 60D), keep 60D as the short-term option.
3. **Top OHLC bar** — display Open/High/Low/Close + change% for the bar under the crosshair (or latest bar on no-hover). The single biggest "I can actually read this chart" affordance TradingView has that we don't.

## Why

The 60D default makes the chart feel like a snapshot, not a tool. Users typically want trend context; switching to 1Y is the first thing they do every time. Persisting the choice removes the click. 5Y and All unblock "show me the long arc" use cases (KO, AAPL, mature names). YTD answers "how am I doing this year" — the most common annual question.

The OHLC bar is the biggest UX gap. Currently to read a specific bar's values the user must hover and wait for a tooltip; the values aren't in the user's natural field of view (top of chart). TradingView shows them as inline text at the chart top, updating in real-time on crosshair move.

## Architecture

### 1. Default period + localStorage

**Frontend** (`marketpulse/web/static/chart.js`):

```javascript
const PERIOD_STORAGE_KEY = "mp.chartPeriod";
const VALID_PERIODS = new Set(["60d", "6m", "ytd", "1y", "5y", "all"]);

function readStoredPeriod() {
  const v = localStorage.getItem(PERIOD_STORAGE_KEY);
  return VALID_PERIODS.has(v) ? v : "1y";
}

function writeStoredPeriod(p) {
  if (VALID_PERIODS.has(p)) localStorage.setItem(PERIOD_STORAGE_KEY, p);
}
```

On `DOMContentLoaded`: read stored period, apply active styling to the matching button, load chart with it.

On button click: write to localStorage, then load.

**Template** (`stock.html`): remove the hardcoded `bg-slate-900 text-white` on the 60D button. JS applies it after init.

### 2. New period buttons (YTD / 5Y / All; drop 30D)

**Backend** (`marketpulse/web/routes/stock.py`):

```python
_VALID_PERIODS = {"60d", "6m", "ytd", "1y", "5y", "all"}
_PERIOD_DAYS_FIXED = {"60d": 60, "6m": 180, "1y": 365, "5y": 1825}
# ytd computed at request time; all uses a generous max (e.g., 30 years).
```

For `period in {"60d", "6m", "ytd", "1y"}`: existing Tencent path (`get_history(ticker, period="1y")` already covers ≤ 1y, slice by cutoff).

For `period in {"5y", "all"}`: switch to yfinance — use the existing `YFinanceClient.fetch_history_range(ticker, start=..., end=date.today())`. Same `_build_payload` for indicators. Cache headers identical.

YTD cutoff: `date(date.today().year, 1, 1)`.

All cutoff: `date(1900, 1, 1)` (yfinance returns from earliest available).

**Template** (`stock.html`): replace the 4 buttons with 6:

```html
<button data-period="60d" class="px-2 py-1 rounded border border-slate-200">60D</button>
<button data-period="6m"  class="px-2 py-1 rounded border border-slate-200">6M</button>
<button data-period="ytd" class="px-2 py-1 rounded border border-slate-200">YTD</button>
<button data-period="1y"  class="px-2 py-1 rounded border border-slate-200">1Y</button>
<button data-period="5y"  class="px-2 py-1 rounded border border-slate-200">5Y</button>
<button data-period="all" class="px-2 py-1 rounded border border-slate-200">All</button>
```

The "All" button may trigger a 5-30 second yfinance call on long-history tickers like KO. Acceptable for an explicit user action; the loading dot we already have communicates wait state.

### 3. Top OHLC bar

**Template** (`stock.html`): add an OHLC bar above the chart container. Placeholders for values; JS fills:

```html
<div id="chart-ohlc-bar" class="text-xs flex gap-3 text-slate-600 mb-1 font-mono">
  <span><span class="text-slate-400">O</span> <span data-ohlc="open">—</span></span>
  <span><span class="text-slate-400">H</span> <span data-ohlc="high">—</span></span>
  <span><span class="text-slate-400">L</span> <span data-ohlc="low">—</span></span>
  <span><span class="text-slate-400">C</span> <span data-ohlc="close">—</span></span>
  <span data-ohlc="change" class="font-semibold"></span>
</div>
```

**Frontend** (`chart.js`):

```javascript
function updateOhlcBar(bar) {
  const el = document.getElementById("chart-ohlc-bar");
  if (!el || !bar) return;
  el.querySelector('[data-ohlc="open"]').textContent  = bar.open.toFixed(2);
  el.querySelector('[data-ohlc="high"]').textContent  = bar.high.toFixed(2);
  el.querySelector('[data-ohlc="low"]').textContent   = bar.low.toFixed(2);
  el.querySelector('[data-ohlc="close"]').textContent = bar.close.toFixed(2);
  const change = bar.close - bar.open;
  const pct = bar.open !== 0 ? (change / bar.open) * 100 : 0;
  const changeEl = el.querySelector('[data-ohlc="change"]');
  changeEl.textContent = `${change >= 0 ? "+" : ""}${change.toFixed(2)} (${change >= 0 ? "+" : ""}${pct.toFixed(2)}%)`;
  changeEl.className = "font-semibold " + (change >= 0 ? "text-green-600" : "text-red-600");
}

// In renderCharts, after creating candleSeries and setting data:
s.mainChart.subscribeCrosshairMove(param => {
  const bar = param.seriesData?.get(s.candleSeries);
  if (bar) updateOhlcBar(bar);
  else if (s.bars.length > 0) updateOhlcBar(s.bars[s.bars.length - 1]);  // fallback to latest
});
// Initial: show latest bar.
if (s.bars.length > 0) updateOhlcBar(s.bars[s.bars.length - 1]);
```

`param.seriesData.get(seriesHandle)` returns the bar at the crosshair position. `param.time` is undefined when crosshair is off-chart. The fallback to latest bar keeps the bar populated when the user isn't hovering — same as TradingView.

## Components

### Files modified

- `marketpulse/web/routes/stock.py` — extend `_VALID_PERIODS`, add YTD/5Y/All logic, swap to yfinance for periods > 1y
- `marketpulse/web/templates/stock.html` — add OHLC bar element, change period buttons (remove 30D, add YTD/5Y/All)
- `marketpulse/web/static/chart.js` — localStorage period memory, OHLC bar updater, crosshair subscription
- `tests/web/test_stock.py` — extend chart-data tests for new periods

## Edge Cases

| Case | Behavior |
|---|---|
| `localStorage` unavailable (private browsing) | `readStoredPeriod` returns default "1y", `writeStoredPeriod` silently fails. No crash. |
| Stored period is one we dropped (e.g., "30d") | `VALID_PERIODS.has("30d")` is false → fall back to "1y" |
| YTD on Jan 1 | cutoff = today; `cutoff - today = 0 days`; returns empty `bars` if no trades yet this year. Acceptable. |
| All on a newly-listed ticker (QUBT IPO 2018) | yfinance returns ~7 years; chart shows full history. Lazy-load disabled because we're already at earliest. |
| All on a delisted ticker | yfinance may return partial history with old end date. `_build_payload` returns it as-is. No new edge case. |
| Crosshair moves off-chart | `param.seriesData.get()` returns undefined → fall back to latest bar |
| Lazy-load prepends bars while user is hovering | New bars are prepended, crosshair position re-resolves naturally (lightweight-charts handles this) |
| Initial bars list is empty | `s.bars[s.bars.length - 1]` is undefined; `updateOhlcBar` short-circuits on `!bar` |

## Tests

`tests/web/test_stock.py`:

1. `test_chart_data_ytd_returns_year_to_date`: hit `?period=ytd`, assert bars span at most from Jan 1 of current year to today
2. `test_chart_data_5y_uses_yfinance`: monkeypatch `YFinanceClient.fetch_history_range`, hit `?period=5y`, assert the mock was called with start ≈ today - 5 years
3. `test_chart_data_all_uses_yfinance_from_1900`: same pattern, assert start ≤ 1900-01-01
4. `test_chart_data_rejects_30d`: hit `?period=30d`, expect 422
5. `test_chart_data_rejects_invalid_period`: hit `?period=foo`, expect 422 with the new valid-period list in the error

Frontend has no test harness. Manual verification post-deploy:
- Open /stock/AAPL → chart loads 1Y by default
- Click 5Y → loads, chart shows full 5 years (~1250 bars). Reload page → still 5Y (localStorage)
- Hover over any bar → OHLC bar updates. Move off → shows latest bar
- Click All → loads slower (yfinance), full history visible
- Click YTD → today's year only

## File Manifest

**Modified:**
- `marketpulse/web/routes/stock.py`
- `marketpulse/web/templates/stock.html`
- `marketpulse/web/static/chart.js`
- `tests/web/test_stock.py`

**Unchanged:**
- Backend lazy-load path (`_chart_data_lazy`) — independent
- Trades page, recap page, etc.

## Risk

**Low.** Three independent additions. Defaults change is reversible by clearing localStorage. New period buttons are additive — old URLs with `?period=30d` will 422 (the one breaking change). Acceptable because 30D was rarely useful and no permalink contract was promised.

YFinance dependency on 5Y/All means slower load (~3-10s) and dependence on Mihomo proxy — but we already use yfinance for lazy-load, so no new failure mode.

## Out of Scope

- Right-side trade panel like TradingView (no execution in scope)
- Drawing tools
- Replay mode
- Performance / Seasonals side panels
- Crosshair sync to RSI/MACD panes (existing syncPair handles time-axis sync; crosshair sync is a separate feature)
- Adaptive chunk size in lazy-load (kept fixed at 180; tunable later if needed)

## Principles Compliance

Per `docs/PRINCIPLES.md`:

- **#1 (Measure, don't auto-modify):** localStorage write only on user click. No silent default changes. ✓
- **#5 (Determinism):** same period + same date → same payload; periods are pure functions of `_PERIOD_DAYS_FIXED` and `date.today()`. ✓

Other principles not relevant to this scope.
