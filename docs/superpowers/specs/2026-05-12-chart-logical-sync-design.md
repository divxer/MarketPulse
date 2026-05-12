# Chart Cross-Pane Logical Sync — Design Spec

**Status:** Approved, ready for implementation plan
**Author:** harvey
**Date:** 2026-05-12

## Goal

Eliminate the K-line chart lazy-load cascade loop at its root by replacing the cross-pane time-range sync (which is fragile under `setData`) with logical-range sync, and by whitespace-filling line/histogram series so their logical indices align with the main candle series.

## Root Cause Recap

Browser test (2026-05-12 04:13) on `/stock/AAPL` traced one prepend cycle in detail:

1. `candle.setData(prepended bars)` → main auto-shifts logical range `{60,120}` → `{240,300}` and preserves time range. **Correct.**
2. `rsi.setData(prepended dense data)` → RSI preserves logical range but its cached time range now points at older dates (because the data array is longer and indices 60-120 in it refer to older bars). **Inconsistent with candle's behavior.**
3. `syncPair` (subscribes to `visibleTimeRangeChange`) fires from RSI's stale-cached time range → calls `mainChart.setVisibleRange(older time range)` → main snaps back from `{240,300}` to `{60,120}` → `barsBefore=0` → cascade.

The mismatch is two-fold:
- **Candle vs line `setData` behavior differs** under prepend (candle shifts logical, line preserves logical).
- **Densified line series have shorter arrays** than candle (RSI drops 13-14 leading nulls), so their logical indices don't map 1:1 to candle's even when they ARE in sync.

## Architecture

Two changes, both in `marketpulse/web/static/chart.js`:

### Change 1: Whitespace-fill instead of densify

Replace the existing `densify(series)` (filters out `null`/`undefined`) with `withWhitespace(series)`:

```javascript
function withWhitespace(series) {
  return series.map(p =>
    (p.value === null || p.value === undefined)
      ? { time: p.time }            // lightweight-charts whitespace item
      : p
  );
}
```

Lightweight-charts v4 line and histogram series natively accept `{ time: T }` items as whitespace — they reserve the bar's slot on the time axis but don't draw. Visually identical to current dense rendering (the line still starts at the first valid value); semantically the line series array now has the same length as the candle array, so logical indices align.

Call sites updated: every `setData(densify(...))` in `renderCharts` and every `setData(densify(...))` inside `prependChunk`. ~10 call sites.

The constant-line overlays (`ob`/`os` at RSI 70/30) currently iterate over `rsiData`. After this change they'll iterate over whitespace-filled rsi, producing whitespace at leading bars — visually identical (no horizontal line drawn there). No behavior change.

### Change 2: syncPair uses logical-range, not time-range

Replace:

```javascript
const syncPair = (a, b) => {
  a.timeScale().subscribeVisibleTimeRangeChange(r => {
    if (!r) return;
    b.timeScale().setVisibleRange(r);
  });
};
```

With:

```javascript
const syncPair = (a, b) => {
  a.timeScale().subscribeVisibleLogicalRangeChange(r => {
    if (!r) return;
    b.timeScale().setVisibleLogicalRange(r);
  });
};
```

With Change 1 ensuring matching array lengths, logical-range sync is now correct cross-pane. And because logical-range survives `setData(prepended)` consistently across candle and line series, the cascade trigger from time-range cache mismatch disappears.

### What stays

- `loadMoreHistory`'s explicit `setVisibleLogicalRange({prevRange.from + chunkLen, ...})` shift stays as defense-in-depth. With logical sync the chart should auto-shift correctly, but the explicit call is idempotent in the correct case and corrective in any edge case.
- `loadingMore` in-flight guard, state-identity check, `barsBefore < 50` trigger, initial-view anchor, `console.debug` lines — all unchanged.
- Backend `/chart-data` API, `freshState()`, `showLoadingDot`, period/ticker switch handlers — all unchanged.
- 293 backend tests unchanged (no Python touched).

## Data Flow (one prepend cycle after fix)

```
state: bars.length=60, visibleRange={0,60}, barsBefore=0
  → triggers loadMoreHistory
  → prevRange = {0,60}, fetch begins
fetch returns 180 bars
  → bars.length becomes 240
  → prependChunk:
      candle.setData(240 bars) → main auto-shifts to {180,240}, time preserved
      vol.setData(240 bars)    → same
      ema*.setData(240 with whitespace) → auto-shifts to {180,240}
      sma*.setData(240 with whitespace) → same
      bb_*.setData(240 with whitespace) → same
      rsi.setData(240 with whitespace) → RSI chart auto-shifts to {180,240}
      macd*.setData(...) → MACD chart auto-shifts to {180,240}
      syncPair fires logical: main↔rsi↔macd, all already at {180,240}, idempotent
  → explicit shift: main.setVisibleLogicalRange({180,240}), no-op
  → barsBefore for {180,240} = 180 (or close), >= 50, no re-trigger
  → loadingMore = false, dot hides
QUIET until user scrolls left to barsBefore < 50.
```

## Components

**Modified:** `marketpulse/web/static/chart.js`

- Rename `densify` → `withWhitespace`, change body
- Update 10 call sites (initial `setData` for ema12/ema26/sma50/sma200/bb_upper/bb_lower/rsi/macd_line/macd_signal/macd_histogram)
- Update `extendAndSet` in `prependChunk` to use `withWhitespace`
- Update `s.macdLineSeries.setData(...)`, `s.macdSignalSeries.setData(...)`, `s.macdHistSeries.setData(...)` inside `prependChunk` to use `withWhitespace`
- Change `syncPair`: `subscribeVisibleTimeRangeChange` → `subscribeVisibleLogicalRangeChange`, `setVisibleRange` → `setVisibleLogicalRange`

**Unchanged:** everything else.

## Edge Cases

| Case | Behavior |
|---|---|
| Indicator with leading nulls (SMA200 has 199) | Whitespace items reserve the time slots; the line just doesn't draw there. Same visual as before. |
| Ticker with very short history (< 14 days) | RSI has all nulls → all whitespace items → no line drawn. Same as current densify-to-empty behavior. |
| Period switch | New `renderCharts` → new state, all series re-created with whitespace-fill data. No cross-pane drift. |
| User scrolls on RSI pane | RSI fires logical-range change → syncPair → main and macd get same logical range. Now consistent (was broken before because RSI's logical referred to different bars). |
| MACD histogram | `setData` accepts whitespace items for histogram series too (per TVC v4 docs). No special handling. |
| RSI ob/os reference lines | Iterates over whitespace-filled rsi → produces whitespace at leading bars → no horizontal line where there's no rsi. Same visual. |

## Testing

Backend: 293/293 unchanged.

Manual (browser DevTools open, Network filter `chart-data`):

1. Open `/stock/AAPL`. Initial: 1 `?period=60d` request, no immediate `?before=` cascade. Console: zero `mp-chart` lines.
2. Mouse-wheel scroll left ~15 bars. Console shows ONE `mp-chart loadMore →` then ONE `mp-chart loadMore ✓`. Network: ONE `?before=` request. Chart adds older bars, visible window stays on same calendar dates.
3. Inspect state: `window.__mpChartState.mainChart.timeScale().getVisibleLogicalRange()` shows the shifted range (e.g., `{180, 240}` after one prepend). `barsBefore >= 50`.
4. Keep scrolling left until ticker IPO reached. Network shows requests only when user actively scrolls. Final state: `hasMoreHistory: false`, no more requests.
5. Click 30D period button. State resets, no leaked in-flight fetches.
6. Resize browser. Bars stretch via `autoSize`, no flicker, no extra fetches.

Acceptance: total network requests for steps 1-2 ≤ 2 (was 31-37 on broken versions). Cross-pane sync visually OK (RSI and MACD scroll with main).

## Out of Scope

- Replacing `addLineSeries`/`addHistogramSeries` with the official `IPaneApi.attachPrimitive` v5 API (project is on v4)
- Removing the explicit shift in `loadMoreHistory` (kept as belt-and-suspenders)
- Frontend test harness (defer to a separate effort)
- Replacing the `subscribeVisibleLogicalRangeChange` lazy-load trigger metric (`barsBefore` works; only the cross-pane sync mechanism is changing)

## Risk

**Low.** Two semantically narrow changes in one file:

- Whitespace fill: lightweight-charts feature documented and stable since v3. No behavioral surprises observed in upstream issues.
- Logical-range sync: simpler than time-range sync (no internal time→logical conversion needed). Idempotent during prepend because all panes share the same logical range with aligned arrays.

If it doesn't work, revert is one commit.
