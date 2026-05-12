# Chart Cross-Pane Logical Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan. Steps use `- [ ]` checkbox syntax.

**Goal:** Fix the cascade-loop root cause by (a) replacing `densify` with whitespace-fill so line/histogram series logical indices align with the candle series, and (b) switching cross-pane `syncPair` from time-range to logical-range subscription.

**Architecture:** Two minimally-coupled edits to `marketpulse/web/static/chart.js`. `densify` becomes `withWhitespace` (replaces null entries with `{time: T}` whitespace items instead of filtering them out). `syncPair` switches from `subscribeVisibleTimeRangeChange`/`setVisibleRange` to `subscribeVisibleLogicalRangeChange`/`setVisibleLogicalRange`.

**Tech Stack:** lightweight-charts v4 whitespace data items, vanilla JS. No backend changes.

**Spec:** [`docs/superpowers/specs/2026-05-12-chart-logical-sync-design.md`](../specs/2026-05-12-chart-logical-sync-design.md)

**Branch:** create new `fix/chart-logical-sync` off `main` (PR #19's branch is independent; this builds on the latest main which has PR #19 merged).

---

## Pre-flight check

- [ ] **Step 0a: Confirm on a fresh branch off latest main**

```bash
git checkout main
git pull
git checkout -b fix/chart-logical-sync
```

Expected: clean checkout, branch created.

- [ ] **Step 0b: Confirm baseline tests pass**

Run: `uv run pytest 2>&1 | tail -3`
Expected: `293 passed`. If not, stop.

- [ ] **Step 0c: Confirm starting file size**

Run: `wc -l marketpulse/web/static/chart.js`
Expected: a line count around 360-370 (it's a single-file vanilla JS chart module).

---

## Task 1: Replace `densify` with `withWhitespace`

Rename the function and change its body so `null`/`undefined` entries become whitespace items `{time: p.time}` instead of being dropped. Logical indices then align between candle and line series.

**Files:** modify only `marketpulse/web/static/chart.js`

- [ ] **Step 1a: Edit the function definition**

Find this near the top of the IIFE (around line 40):

```javascript
  function densify(series) {
    return series.filter(p => p.value !== null && p.value !== undefined);
  }
```

Replace with:

```javascript
  // Convert null/undefined indicator points into lightweight-charts
  // whitespace items ({time: T}). The series array length now matches
  // the candle series, so logical indices align across all panes — this
  // is what allows syncPair to use logical-range subscription (see below)
  // and what prevents the prepend-cascade loop diagnosed on 2026-05-12.
  // Whitespace items reserve the bar's time-axis slot without drawing,
  // so the visual result is identical to filtering them out.
  function withWhitespace(series) {
    return series.map(p =>
      (p.value === null || p.value === undefined) ? { time: p.time } : p
    );
  }
```

- [ ] **Step 1b: Rename all call sites**

Run: `grep -n 'densify(' marketpulse/web/static/chart.js`
Expected: 11 call sites (1 definition + 10 invocations).

Replace `densify(` with `withWhitespace(` at all invocation sites. The simplest reliable way:

```bash
# Note: only one definition site exists, already changed in Step 1a.
# This sed updates the remaining call sites.
sed -i '' 's/densify(/withWhitespace(/g' marketpulse/web/static/chart.js
```

Then verify:

```bash
grep -n 'densify' marketpulse/web/static/chart.js
```

Expected: NO matches (the old name is fully gone).

```bash
grep -nc 'withWhitespace' marketpulse/web/static/chart.js
```

Expected: 11 (1 definition + 10 call sites).

- [ ] **Step 1c: Verify backend tests still pass**

Run: `uv run pytest 2>&1 | tail -3`
Expected: `293 passed`.

- [ ] **Step 1d: Sanity-check the file parses**

Run: `node --check marketpulse/web/static/chart.js`
Expected: no output (success). If node isn't available, skip — `pytest` already loaded the static file via test routes if any cover it; otherwise the next deploy will catch syntax errors.

- [ ] **Step 1e: Commit**

```bash
git add marketpulse/web/static/chart.js
git commit -m "$(cat <<'EOF'
refactor(chart): replace densify() with withWhitespace() for indicator series

lightweight-charts v4 line and histogram series accept whitespace items
({time: T} with no value field) to reserve a time-axis slot without
drawing. Using these instead of filtering null entries means RSI, MACD,
and the indicator overlays (EMA/SMA/BB) all have the same array length
as the candle series — so their logical indices map 1:1 to bar positions
in the dataset.

This is a prerequisite for the next commit, which switches syncPair from
time-range subscription (fragile under setData because cached time ranges
become inconsistent between candle and line series after prepend) to
logical-range subscription (correct only when all panes have aligned
indices).

No visual change: whitespace items don't draw, so lines still start at
their first valid value just like with densify-then-filter.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Switch `syncPair` to logical-range

Cross-pane sync now uses logical-range subscription. With Task 1 ensuring index alignment, this is correct and idempotent across prepends.

**Files:** modify only `marketpulse/web/static/chart.js`

- [ ] **Step 2a: Edit syncPair**

Find this block (around line 178-183):

```javascript
    // Sync time scale across panes (main ↔ RSI ↔ MACD).
    const syncPair = (a, b) => {
      a.timeScale().subscribeVisibleTimeRangeChange(r => {
        if (!r) return;
        b.timeScale().setVisibleRange(r);
      });
    };
```

Replace with:

```javascript
    // Sync time scale across panes (main ↔ RSI ↔ MACD) via LOGICAL range.
    // Time-range sync was fragile under setData(prepended): candle.setData
    // auto-shifts logical range and preserves time range, but line.setData
    // preserves logical and lets time range shift to older bars — the
    // mismatch made syncPair propagate a stale time range and snap main
    // back to the left edge, causing the cascade loop. Logical sync is
    // correct because withWhitespace() (above) keeps all panes' arrays
    // the same length, so logical index N means the same bar everywhere.
    const syncPair = (a, b) => {
      a.timeScale().subscribeVisibleLogicalRangeChange(r => {
        if (!r) return;
        b.timeScale().setVisibleLogicalRange(r);
      });
    };
```

- [ ] **Step 2b: Verify no leftover time-range subs**

Run: `grep -n 'subscribeVisibleTimeRangeChange\|setVisibleRange' marketpulse/web/static/chart.js`
Expected: NO matches (both APIs gone).

- [ ] **Step 2c: Verify backend tests still pass**

Run: `uv run pytest 2>&1 | tail -3`
Expected: `293 passed`.

- [ ] **Step 2d: Sanity-check file parses**

Run: `node --check marketpulse/web/static/chart.js`
Expected: no output. Skip if node unavailable.

- [ ] **Step 2e: Commit**

```bash
git add marketpulse/web/static/chart.js
git commit -m "$(cat <<'EOF'
fix(chart): syncPair uses logical-range subscription, not time-range

Time-range sync between main/RSI/MACD panes was the root cause of the
prepend cascade loop diagnosed on 2026-05-12. Detailed timeline:

t=51381: candle.setData(prepended) → main auto-shifts logical {60,120}→
         {240,300}, time preserved (correct candle behavior)
t=51387: rsi.setData(prepended) → rsi preserves logical {60,120}, but
         its cached time range now points at OLDER bars (line series
         behavior differs from candle's)
t=51387: syncPair (subVTRC) fires from rsi → mainChart.setVisibleRange
         (rsi's stale older time range) → main snaps to {60,120} again
         → barsBefore=0 → cascade resumes

Logical sync sidesteps the entire issue: with withWhitespace() (prior
commit) aligning all panes' array lengths, logical index N references
the same bar in every pane. Subscribing to logical-range changes and
forwarding the same logical range is correct and idempotent across
setData calls — neither candle nor line series logical ranges "drift"
in mismatched ways.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Push and open PR

- [ ] **Step 3a: Push the branch**

Run: `git push -u origin fix/chart-logical-sync`
Expected: branch published.

- [ ] **Step 3b: Open the PR**

Run:

```bash
gh pr create --title "fix(chart): cross-pane logical sync (kills cascade loop at root)" --body "$(cat <<'EOF'
## Summary

PR #19 left a remnant cascade loop that browser-test on 2026-05-12 revealed: 31-37 \`?before=\` requests on a single page load. Root-cause investigation traced it to a **cross-pane sync mismatch** between candle and line series \`setData\` behavior under prepend, propagated via the time-range \`syncPair\`.

This PR fixes the root cause with two coordinated changes:

1. **\`densify\` → \`withWhitespace\`**: indicator series now emit lightweight-charts whitespace items (\`{time: T}\`) for null values instead of filtering them out. Visual output is identical; logical indices now align across all panes (candle, ema/sma/bb on main, rsi, macd-line/signal/histogram).

2. **\`syncPair\` switches to logical-range subscription**: with aligned indices from change 1, logical-range sync is correct cross-pane. It's also immune to the \`setData\`-induced time-range cache drift that broke time-range sync.

## Why this is different from PR #19

PR #19 changed the lazy-load trigger (\`range.from\` → \`barsBefore\`) and added an explicit shift after prepend. Both correct. But \`syncPair\` was still propagating stale time ranges back to main during prepend, snapping main's view to the left edge and re-triggering. This PR removes that propagation path entirely.

Spec: \`docs/superpowers/specs/2026-05-12-chart-logical-sync-design.md\`

## Test Plan

Backend: 293/293 pass (no Python changes), \`ruff check\` clean.

Manual after deploy (DevTools Network filter \`chart-data\`, Console filter \`mp-chart\`):

- [ ] Open \`/stock/AAPL\`. Expect: 1 \`?period=60d\` request, no immediate \`?before=\` cascade. Console: zero \`mp-chart\` lines (no auto-load on first paint).
- [ ] Mouse-wheel scroll left ~15 bars. Expect: 1 \`mp-chart loadMore →\` then 1 \`mp-chart loadMore ✓\`. 1 new \`?before=\` request. Older bars appear, calendar dates of visible window unchanged.
- [ ] Check \`window.__mpChartState.mainChart.timeScale().getVisibleLogicalRange()\` — shifted by chunk size, \`barsBefore >= 50\`.
- [ ] Continue scrolling. Each fetch triggered by user motion only. Eventually \`hasMoreHistory: false\` (near IPO date).
- [ ] Click 30D button. State resets cleanly.
- [ ] Resize browser. Bars stretch (autoSize), no flicker, no extra fetches.

Acceptance: total \`chart-data\` requests for steps 1-2 ≤ 2 (was 31-37 pre-fix).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed.

- [ ] **Step 3c: Self-review checklist**

Run each:

- `git log --oneline | head -3` — should show Task 1, Task 2 commits ahead of main
- `uv run pytest 2>&1 | tail -3` — `293 passed`
- `uv run ruff check 2>&1 | tail -3` — clean
- `grep -c densify marketpulse/web/static/chart.js` — `0`
- `grep -c withWhitespace marketpulse/web/static/chart.js` — `11`
- `grep -c subscribeVisibleTimeRangeChange marketpulse/web/static/chart.js` — `0`
- `grep -c subscribeVisibleLogicalRangeChange marketpulse/web/static/chart.js` — `2` (one in syncPair, one in lazy-load trigger)
- `grep -c setVisibleRange marketpulse/web/static/chart.js` — `0` (only `setVisibleLogicalRange` should remain)
- `grep -c setVisibleLogicalRange marketpulse/web/static/chart.js` — `3` (initial anchor, post-prepend shift, syncPair)

Report PR URL.
