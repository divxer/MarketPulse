# K-Line Chart Lazy-Load — Design Spec

**Status:** Approved, ready for implementation plan
**Author:** harvey
**Date:** 2026-05-12

## Goal

Mimic TradingView's infinite-scroll behavior on the K-line chart: when the user mouse-wheel scrolls toward the left edge, the chart automatically fetches earlier historical data and prepends it to the visible series. No period button switching required; no visible loading spinner; scroll position is preserved as new data arrives.

Right side behavior unchanged — daily K-line ends at "today" (the latest closed session).

## Why now

Currently the chart loads N days based on the period selector (30D / 60D / 6M / 1Y) and the user cannot see anything beyond that range without clicking a different button. Scrolling left past the loaded range reveals blank space. TradingView and similar professional charting apps handle this with lazy-loading on scroll, and the user wants the same feel here.

## Architecture

1. **Initial load unchanged.** First page render still hits `GET /stock/{ticker}/chart-data?period=60d` (or whatever period the button selects), which goes through Tencent's `Usfqkline` endpoint — fast, China-friendly, capped at ~1200 trading days (~4.75 years).
2. **Lazy-load via new endpoint params.** Scrolling near the left edge triggers `GET /stock/{ticker}/chart-data?before=<YYYY-MM-DD>&count=180`. This new code path uses **yfinance** (the only source that supports arbitrary historical date ranges).
3. **Backend pads indicators with lookback.** To compute SMA200 / EMA26 / Bollinger correctly inside the requested 180-day window, yfinance fetches `180 + 250 = 430` days before `before`, then the response trims indicators to the 180-day window before returning.
4. **Frontend prepends.** Client maintains a module-level state (`window.__mpChartState`) with all loaded data. On lazy-load success, prepend new bars/indicators and call `series.setData(combined)` for each. Lightweight-charts preserves the visible time range across `setData()`, so the user's scroll position doesn't jump.

## Components

### Backend

**`marketpulse/data/yfinance_client.py::fetch_history_range`** — new method

```python
def fetch_history_range(
    self, ticker: str, *, start: date, end: date,
) -> list[Bar]:
    """Daily OHLCV bars from yfinance for an explicit date window.

    Used by the chart-data endpoint's lazy-load path. Inclusive of start
    and end. Returns Bars sorted oldest-first.
    """
```

Mirrors `TencentClient.fetch_history` shape but accepts explicit dates instead of a period string.

**`marketpulse/web/routes/stock.py::chart_data`** — extended

```python
@router.get("/stock/{ticker}/chart-data")
def chart_data(
    ticker: str,
    period: str | None = None,        # existing
    before: str | None = None,        # NEW (YYYY-MM-DD)
    count: int = 180,                 # NEW
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    """Two modes:
    - period=... → current behavior (Tencent + indicators on the full slice)
    - before=... → fetch `count` days ending before `before` via yfinance,
                   pad with 250-day lookback for indicator computation,
                   trim indicators to the requested window before responding
    `before` takes precedence if both are given (lazy-load mode).
    """
```

**Indicator helper** — extract / reuse existing signals computation. The existing route already runs `bollinger_series`, `ema`, `macd`, `rsi_series`, `sma`, `scan_signal_markers` over the full bar list. For lazy-load mode we need to:
1. Fetch `bars_padded` = `[bars_lookback_250d] + [bars_requested_180d]` via yfinance
2. Run all indicator functions over `bars_padded`
3. **Trim** every output series by **date range**, keeping only points whose `time >= bars_requested_180d[0].time`. Trimming by array index would misalign indicators that have leading-null padding (e.g. SMA200 has 199 leading nulls); trimming by time is robust to that.

The trimming logic lives inside `chart_data` (small block — the heavy lifting is the existing signals.py functions).

### Frontend (`marketpulse/web/static/chart.js`)

**New module-level state**:

```javascript
window.__mpChartState = {
  ticker: null,
  bars: [],
  ema12: [], ema26: [], sma50: [], sma200: [],
  bb_upper: [], bb_lower: [],
  rsi: [],
  macd: { line: [], signal: [], histogram: [] },
  signal_markers: [],
  oldestLoaded: null,    // ISO YYYY-MM-DD of leftmost loaded bar
  hasMoreHistory: true,  // false after a fetch returns empty bars
  loadingMore: false,    // in-flight guard
  mainChart: null,       // chart handle for setData()
  candleSeries: null,    // series handles
  rsiChart: null, rsiSeries: null,
  macdChart: null, macdLineSeries: null, macdSignalSeries: null, macdHistSeries: null,
  volSeries: null,
  emaSeries: {...}, smaSeries: {...}, bbSeries: {...},
  // (full set of series handles needed for setData)
};
```

**Trigger**: subscribe to the main chart's `visibleLogicalRangeChange`. When `range.from < 30` (leftmost visible bar is within 30 bars of `bars[0]`), call `loadMoreHistory()`.

**`loadMoreHistory()`**:

```javascript
async function loadMoreHistory() {
  const s = window.__mpChartState;
  if (s.loadingMore || !s.hasMoreHistory) return;
  s.loadingMore = true;
  const tickerAtRequest = s.ticker;  // capture for staleness check
  try {
    const r = await fetch(
      `/stock/${s.ticker}/chart-data?before=${s.oldestLoaded}&count=180`
    );
    if (!r.ok) return;  // network failure; will retry next scroll
    const chunk = await r.json();
    if (s.ticker !== tickerAtRequest) return;  // user switched ticker mid-fetch
    if (!chunk.bars || chunk.bars.length === 0) {
      s.hasMoreHistory = false;
      return;
    }
    // Prepend to every series and call setData on each handle
    prependChunk(chunk);
    s.oldestLoaded = chunk.bars[0].time;
  } catch (exc) {
    console.warn("lazy-load failed:", exc);
  } finally {
    s.loadingMore = false;
  }
}
```

**`prependChunk(chunk)`**: extends each array in `state` with `chunk.<field>` in front, then calls `setData(state.<field>)` on the corresponding series handle. Lightweight-charts preserves the visible time range across this.

**State reset**: `renderCharts(payload)` (the existing initial-render function) reinitializes `window.__mpChartState` — sets `ticker`, copies all initial arrays from `payload`, sets `oldestLoaded = bars[0].time`, `hasMoreHistory = true`, `loadingMore = false`, stores series handles.

## Data Flow

```
Initial:
  user opens /stock/QUBT
  → GET /chart-data?period=60d (Tencent)
  → render 60D + init state.oldestLoaded = (today - 60 trading days)

User scrolls left:
  visibleLogicalRangeChange fires, range.from = 25
  → 25 < 30 threshold → loadMoreHistory()
  → GET /chart-data?before=2026-03-13&count=180 (yfinance + indicators)
  → server fetches 430 days ending 2026-03-13, computes indicators, trims
     to last 180 days, returns
  → client prepends 180 bars + indicators
  → setData(combined) on each series
  → state.oldestLoaded = chunk.bars[0].time  (~2025-09-15)
  → visible time range unchanged; user sees new bars appear on the left

User keeps scrolling:
  ... repeats ...
  eventually yfinance returns empty (ticker IPO'd)
  → state.hasMoreHistory = false
  → no more fetches
```

## Edge Cases

| Case | Behavior |
|---|---|
| yfinance fetch fails (network/Mihomo down) | `loadingMore = false`; `hasMoreHistory` unchanged so next scroll retries. `console.warn`, no user-facing error |
| yfinance returns empty bars (ticker IPO reached) | `hasMoreHistory = false` for this session; no more fetches |
| User scrolls fast / multiple triggers | `loadingMore` guard ensures only one in-flight request |
| Period button clicked during lazy-load | The period switch calls `load(ticker, period)` which calls `renderCharts(payload)` → resets state. In-flight chunk arrives later but is dropped because `state.ticker` may have changed (we capture `tickerAtRequest` and bail on mismatch) |
| Ticker switch | Same as above — `state.ticker` mismatch causes in-flight result to be dropped |
| Indicator lookback short on early bars (ticker very new) | yfinance returns fewer than 250 lookback bars; SMA200 etc. are `null` at the earliest points; existing `densify()` filter on the client already drops nulls |
| Same-day duplicate fetch attempts | Idempotent: each call sends same `before` if user is at same scroll position; in-flight guard prevents the duplicate. Result is unchanged either way |

## Testing

| File | Coverage |
|---|---|
| `tests/web/test_stock.py` (extend) | `chart-data` accepts `before` + `count`; response bars end strictly before `before`; response bar count ≤ `count`; `before` earlier than IPO returns empty bars; auth required |
| `tests/unit/test_yfinance_history.py` (new) | `fetch_history_range(ticker, start, end)` mock-tests `yf.Ticker.history(start, end)` call, return shape |
| `tests/integration/test_chart_data_lazy.py` (new) | Hit `?period=60d` then `?before=<earliest>&count=180`, assert second chunk's bars strictly precede first chunk |

Frontend JS has no test harness (project doesn't ship vitest/jest). Manual verification: open `/stock/AAPL`, scroll left, see new bars appear without a visible spinner.

## File Manifest

**New:**
- `tests/unit/test_yfinance_history.py`
- `tests/integration/test_chart_data_lazy.py`

**Modified:**
- `marketpulse/data/yfinance_client.py` — `fetch_history_range(ticker, start, end)`
- `marketpulse/web/routes/stock.py::chart_data` — accept `before` + `count`; lazy-load path that pads + trims
- `marketpulse/web/static/chart.js` — `window.__mpChartState`, `subscribeVisibleLogicalRangeChange` handler, `loadMoreHistory()`, `prependChunk()`, state reset on `renderCharts()`
- `tests/web/test_stock.py` — extend `chart_data` tests

## Out of Scope

- Right-side intraday/today loading (right edge stays at last closed daily bar)
- Period button preserving scroll position across switches (clicking 30D resets the view)
- localStorage caching across page reloads
- Adaptive chunk size (fixed at 180 days)
- Preloading common history on idle
- Pinch-to-zoom UX changes
- Migration of initial-load path to yfinance (Tencent stays primary for the first 60D/6M/1Y load — it's fast and China-friendly; yfinance is only used for lazy-load chunks because Tencent can't slice arbitrary date ranges)

## Future Optimizations

- **Larger chunk on idle / second prefetch**: when user pauses near the left edge, prefetch another 180 days so the next scroll is instant
- **Browser cache (HTTP Cache-Control)**: historical bars don't change, so `Cache-Control: max-age=86400` on the `before=` response cuts server load on revisits
- **WebSocket for right-side intraday**: subscribe to live Tencent quotes during market hours, append today's bar in real time
- **Indicator computation off the hot path**: precompute SMA200/EMA26 for popular tickers nightly; serve from a cache table
