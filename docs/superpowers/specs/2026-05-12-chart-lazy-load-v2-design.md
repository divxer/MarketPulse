# K-Line Chart Lazy-Load v2 — Design Spec

**Status:** Approved, ready for implementation plan
**Author:** harvey
**Date:** 2026-05-12

## Goal

Replace the current chart lazy-load implementation with TradingView's official `barsInLogicalRange().barsBefore` pattern. End the cascade-fetch loop that PRs #10–#18 chased through six rounds of patches.

## Background

The chart lazy-load feature (PR #12) was followed by five regression fixes (#13, #14, #15, #17, #18). Each chased a symptom of the same root mistake: the trigger threshold used `range.from < 60`, where `range.from` is the leftmost visible bar's *current logical index* in the dataset. After a `setData()` with prepended bars, `range.from` does not preserve its semantic meaning the way the code assumed — it shifts based on lightweight-charts' internal range-update rules, often staying at 0 (or wherever the chart re-fits to), re-firing the subscription, cascading to ticker IPO.

PR #18 attempted "trust setData to preserve time range" — empirically false. Test on 2026-05-12 showed bars.length=2561 and 31 chart-data requests on a single page load.

TradingView's own lightweight-charts documentation provides the correct primitive: `series.barsInLogicalRange(range).barsBefore` — the count of bars in the dataset *earlier than* the visible range. Unlike `range.from`, `barsBefore` is dataset-relative-to-visible-window, so prepend makes it grow monotonically.

## Architecture

Three changes, each independent:

1. **Trigger**: subscription handler reads `candleSeries.barsInLogicalRange(range).barsBefore` instead of `range.from`. Threshold: `< 50` (TradingView's example value).

2. **View restoration after prepend**: `loadMoreHistory` captures `prevRange = mainChart.timeScale().getVisibleLogicalRange()` before fetch, then after `prependChunk()` calls `setVisibleLogicalRange({from: prevRange.from + chunk.bars.length, to: prevRange.to + chunk.bars.length})`. Explicit shift — no reliance on "automatic" behavior.

3. **Initial view anchor**: `renderCharts` end calls `setVisibleLogicalRange({from: Math.max(0, bars.length - 60), to: bars.length})` so the chart opens on the most recent 60 bars rather than fitted to all.

Backend `/chart-data?before=...&count=...` is unchanged. Frontend state structure (`window.__mpChartState`) is unchanged.

## Components

### `marketpulse/web/static/chart.js`

**`renderCharts` (end of function)** — replace the current `setVisibleLogicalRange({from:0, to:bars.length})` block with:

```javascript
// Anchor initial view to most recent ~60 bars. If we show all initial
// bars, barsBefore=0 fires loadMoreHistory immediately on first paint
// (TradingView intends this as a buffer prefetch, but it's visible to
// the user as a request-on-load). Anchoring to 60 means the prefetch
// only happens if the initial dataset is shorter than 60 bars.
s.mainChart.timeScale().setVisibleLogicalRange({
  from: Math.max(0, s.bars.length - 60),
  to: s.bars.length,
});

// Lazy-load trigger: TradingView official pattern using barsInLogicalRange.
// barsBefore = count of bars in the dataset earlier than the visible range.
// Unlike range.from, this value is invariant to prepend: it grows by
// chunk.bars.length after a successful load, so threshold checks remain
// stable across cascading fetches.
s.mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
  if (!range) return;
  const info = s.candleSeries.barsInLogicalRange(range);
  if (info && info.barsBefore < 50) loadMoreHistory();
});
```

**`loadMoreHistory`** — replace body with:

```javascript
async function loadMoreHistory() {
  const s = window.__mpChartState;
  if (!s || s.loadingMore || !s.hasMoreHistory || !s.ticker) return;
  s.loadingMore = true;
  const tickerAtRequest = s.ticker;
  const prevRange = s.mainChart.timeScale().getVisibleLogicalRange();
  showLoadingDot(true);
  try {
    const r = await fetch(
      `/stock/${s.ticker}/chart-data?before=${s.oldestLoaded}&count=180`,
    );
    if (!r.ok) return;
    const chunk = await r.json();
    if (window.__mpChartState !== s) return;       // period/ticker switch
    if (s.ticker !== tickerAtRequest) return;       // paranoid
    if (!chunk.bars || chunk.bars.length === 0) {
      s.hasMoreHistory = false;
      return;
    }
    prependChunk(chunk);
    s.oldestLoaded = chunk.bars[0].time;
    // setData inside prependChunk causes lightweight-charts to refit.
    // Shift the visible logical range right by chunk.bars.length so the
    // user's view stays anchored to the same TIME window. Without this
    // explicit shift, the chart jumps (typically to the start of the
    // expanded dataset) and barsBefore re-falls below threshold, looping.
    if (prevRange) {
      s.mainChart.timeScale().setVisibleLogicalRange({
        from: prevRange.from + chunk.bars.length,
        to: prevRange.to + chunk.bars.length,
      });
    }
  } catch (exc) {
    console.warn("lazy-load failed:", exc);
  } finally {
    s.loadingMore = false;
    showLoadingDot(false);
  }
}
```

**`prependChunk`** — body unchanged except remove the trailing comment block ("No setVisibleLogicalRange needed..."). The setVisibleLogicalRange call now lives in the caller.

**Removed code** — none of these are needed and they're misleading-by-omission:
- The "No fitContent(), no setVisibleLogicalRange" comment block
- The "No rAF wait needed" comment block in `loadMoreHistory`
- The "No setVisibleLogicalRange needed" comment block in `prependChunk`

### Unchanged

- `marketpulse/web/routes/stock.py` — `/chart-data?before=&count=` endpoint
- `marketpulse/data/yfinance_client.py` — `fetch_history_range`
- `marketpulse/web/templates/stock.html` — `#chart-loading-dot` overlay
- `freshState()` structure, `densify()`, `applyToggles()`, `showLoadingDot()`, `load()`, DOMContentLoaded handler
- `syncPair` time-axis sync between main/RSI/MACD charts (independent mechanism, no interaction with lazy-load)
- Backend tests (293/293 pass, no logic change in this PR)

## Data Flow

```
Initial render (60d period, ~60 bars):
  setData(bars) → setVisibleLogicalRange({from: 0, to: 60})
  subscription fires once: barsBefore = 0 < 50 → loadMoreHistory
  (intentional prefetch; loadingMore guard prevents reentry)
  fetch ?before=<oldest>&count=180
  → 180 bars returned, prepend → bars.length = 240
  → setVisibleLogicalRange({from: 180, to: 240})  (shift right by 180)
  subscription fires: range = {180, 240}.
  barsBefore = count of bars in dataset earlier than visible = 180.
  180 >= 50 → no trigger. Quiet.

User scrolls left until barsBefore < 50:
  → loadMoreHistory → fetch another 180 → prepend → shift right by 180
  → barsBefore back to >50, quiet again.

User keeps scrolling until ticker IPO:
  yfinance returns empty bars → hasMoreHistory = false
  no more subscriptions trigger fetches.
```

## Edge Cases

| Case | Behavior |
|---|---|
| Period button clicked | `load()` → `renderCharts()` → new state object via `freshState()`. In-flight chunk arrives later, state-identity check (`window.__mpChartState !== s`) drops it. |
| Ticker switch | Same mechanism. |
| Fast scroll (multiple triggers before first fetch returns) | `loadingMore` guard ensures one in-flight request. Subsequent subscription fires return early. |
| yfinance fetch fails | `loadingMore = false`, `hasMoreHistory` unchanged. Next scroll trigger retries. |
| yfinance returns empty bars (ticker IPO) | `hasMoreHistory = false`. No further fetches this session. |
| `prevRange` is `null` (chart not yet visible) | Skip the shift. Shouldn't happen in practice since trigger requires a visible range, but defensive. |
| Initial dataset shorter than 60 bars | `Math.max(0, ...)` clamps the lower bound. View shows all available bars. |

## Testing

Backend tests (`tests/web/test_stock.py`, `tests/integration/test_chart_data_lazy.py`, `tests/unit/test_yfinance_history.py`) are unchanged and continue to verify the `/chart-data?before=&count=` contract.

Frontend has no test harness (no vitest/jest in this project). Manual verification after deploy:

1. Open `/stock/QUBT`. Chart loads showing ~60 bars (recent dates). Network panel shows: one `?period=60d` request, one immediate `?before=&count=180` prefetch, then quiet.
2. Mouse-wheel scroll left. After scrolling past ~130 bars from the right edge, a new fetch fires. Older bars appear. User's visible window stays anchored on the same calendar dates (no jump).
3. Continue scrolling until requests stop firing (ticker IPO reached). `__mpChartState.hasMoreHistory` should be `false`.
4. Click 30D/6M/1Y period button. New initial render, state resets cleanly, no leaked in-flight fetches.
5. Resize browser window. Bars stretch to fill new width (handled by `autoSize: true`), no flicker, no extra fetches.

## File Manifest

**Modified:**
- `marketpulse/web/static/chart.js` — replace trigger logic, add view-shift in `loadMoreHistory`, anchor initial view, remove stale comment blocks

**Unchanged (verified compatible):**
- All backend files
- `stock.html`
- `chart.js` `freshState`, `prependChunk` body, RSI/MACD sync, period/ticker switch handlers

## Out of Scope

- Tencent → yfinance migration for the initial period load (Tencent stays fast for the China-friendly first paint)
- Pixel-level resize behavior beyond `autoSize: true` (PR #10's "fill empty space on resize" feature stays absent — `autoSize` keeps bars stretched correctly, the resize-fill UX bug from May 12 was a separate observation in a different state)
- Adaptive chunk size, browser cache, WebSocket intraday
- Frontend test harness — defer until there's a second chart-related feature that warrants the setup cost

## Risk Assessment

**Low risk:**
- Three localized edits in one file
- Backend unchanged → existing 293 tests still pass unchanged
- Reverting is a single-commit revert if it doesn't work

**Verification before merge:**
- Tests still pass
- Manual browser test on `/stock/QUBT` confirms: no cascade fetches, scroll left loads older data, view stays anchored

## Why Now Is Different From Past Attempts

| PR | Theory | Why it failed |
|---|---|---|
| #14 | Restore visible range after prepend using captured range | Used `range.from < 60` as trigger — meaning shifts after prepend, re-fires |
| #15 | rAF wait absorbs deferred fit events | Patched a symptom of fitContent's sticky mode; didn't address trigger-metric drift |
| #17 | Replace fitContent with setVisibleLogicalRange to avoid sticky mode | Anchored logical range whose meaning shifts on prepend; cascade persisted |
| #18 | "setData preserves time range when fitContent never called" | Empirically false; chart still refits |
| **v2** | Use `barsBefore` (dataset-stable) for trigger + explicit shift for view | `barsBefore` is invariant to prepend by construction; explicit shift means no reliance on chart's auto-fit semantics |

The key shift: stop trying to read the chart's internal state through `range.from` (whose semantics change between calls) and instead use a metric that's stable across `setData()` boundaries.
