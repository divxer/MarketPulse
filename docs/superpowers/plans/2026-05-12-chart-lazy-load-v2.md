# Chart Lazy-Load v2 Implementation Plan

> ⚠️ **SUPERSEDED.** See [`2026-05-12-chart-logical-sync.md`](2026-05-12-chart-logical-sync.md) for the canonical chart sync plan. The `barsBefore` trigger from this plan is retained in the canonical version; the explicit post-prepend shift was kept too. Only the syncPair mechanism and indicator densification were rewritten on top. Retained for historical record.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `range.from < 60` cascade-prone trigger with TradingView's official `barsInLogicalRange().barsBefore` pattern + explicit visible-range shift after prepend.

**Architecture:** Three focused edits to `marketpulse/web/static/chart.js`. Trigger uses dataset-stable metric (`barsBefore`). `loadMoreHistory` captures `prevRange` before fetch and explicitly shifts the visible range right by `chunk.bars.length` after `prependChunk`. Initial `renderCharts` anchors the view to the most recent 60 bars instead of fitting to all. No backend changes.

**Tech Stack:** lightweight-charts v4 (TradingView), vanilla JS, no test harness. Backend Python untouched.

**Spec:** [`docs/superpowers/specs/2026-05-12-chart-lazy-load-v2-design.md`](../specs/2026-05-12-chart-lazy-load-v2-design.md)

**Branch (already created):** `fix/chart-lazy-load-tradingview-pattern` — spec is already committed there. The plan continues commits on this branch.

---

## Pre-flight check

Before starting, verify state.

- [ ] **Step 0a: Confirm branch**

Run: `git branch --show-current`
Expected: `fix/chart-lazy-load-tradingview-pattern`

- [ ] **Step 0b: Confirm clean working tree**

Run: `git status --short`
Expected: empty (no modified files)

- [ ] **Step 0c: Confirm baseline tests pass**

Run: `uv run pytest 2>&1 | tail -3`
Expected: `293 passed` (no failures, no errors).
If anything else: stop, investigate before touching code.

---

## Task 1: Replace trigger with `barsBefore` + add observability

Replace the existing `subscribeVisibleLogicalRangeChange` handler in `renderCharts`. Use `candleSeries.barsInLogicalRange(range).barsBefore` instead of `range.from`. Add a `console.debug` call so we can see the trigger metric in production.

**Files:**
- Modify: `marketpulse/web/static/chart.js` (the `subscribeVisibleLogicalRangeChange` block near the end of `renderCharts`)

- [ ] **Step 1a: Edit the subscription handler**

Find this block (currently at the end of `renderCharts`, after the initial-view comment block):

```javascript
    // Lazy-load trigger: re-fetch older history when scroll nears left edge.
    s.mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (!range) return;
      // range.from is the LEFTMOST visible logical index relative to bars[0].
      // Trigger fetch when leftmost visible bar is within 60 of bars[0] —
      // gives the yfinance fetch (~1-3s through Mihomo) headroom to land
      // before a fast-scrolling user hits the edge.
      if (range.from < 60) {
        loadMoreHistory();
      }
    });
```

Replace with:

```javascript
    // Lazy-load trigger: TradingView's official barsInLogicalRange pattern.
    // barsBefore = count of bars in the dataset earlier than the visible
    // range. Unlike range.from, this is invariant to prepend — after a
    // chunk lands, barsBefore grows by chunk.bars.length, so the same
    // trigger expression stays stable across cascading loads. Threshold
    // 50 is TradingView's example value; gives the yfinance fetch (~1-3s
    // through Mihomo) headroom before a fast-scrolling user hits the edge.
    s.mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (!range) return;
      const info = s.candleSeries.barsInLogicalRange(range);
      if (info && info.barsBefore < 50) loadMoreHistory();
    });
```

- [ ] **Step 1b: Verify backend tests still pass**

Run: `uv run pytest 2>&1 | tail -3`
Expected: `293 passed`. (Frontend-only edit — should not affect any test.)

- [ ] **Step 1c: Commit**

```bash
git add marketpulse/web/static/chart.js
git commit -m "$(cat <<'EOF'
fix(chart): use barsInLogicalRange.barsBefore for lazy-load trigger

range.from is the leftmost visible bar's CURRENT logical index — its
semantic meaning shifts after every prepend (the chart's auto-fit and
internal range-update rules don't preserve it the way the old code
assumed). That drift is why PRs #14–#18 each thought they'd killed the
cascade-fetch loop but every test showed bars.length climbing to 2500+
and 15-31 chart-data requests per page load.

barsBefore is dataset-relative: it counts how many bars exist in the
series before the visible range. Prepend N bars → barsBefore grows
by N. The trigger expression `barsBefore < 50` therefore stays stable
across cascading loads.

Threshold 50 is TradingView's lightweight-charts example value.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Capture prevRange + explicit shift after prepend

Modify `loadMoreHistory` to capture the visible logical range before fetching, then after `prependChunk` explicitly shift the range right by `chunk.bars.length`. Add `console.debug` lines on entry and on successful prepend so production behavior is observable in DevTools.

**Files:**
- Modify: `marketpulse/web/static/chart.js` (the `loadMoreHistory` function)

- [ ] **Step 2a: Replace `loadMoreHistory` body**

Find the current function (it's the one starting `async function loadMoreHistory()`):

```javascript
  async function loadMoreHistory() {
    const s = window.__mpChartState;
    if (!s || s.loadingMore || !s.hasMoreHistory || !s.ticker) return;
    s.loadingMore = true;
    const tickerAtRequest = s.ticker;
    showLoadingDot(true);
    try {
      const r = await fetch(
        `/stock/${s.ticker}/chart-data?before=${s.oldestLoaded}&count=180`,
      );
      if (!r.ok) return;  // log+retry on next scroll
      const chunk = await r.json();
      // State-object identity check catches period switches too — period
      // switch creates a new state object via freshState(), so an in-flight
      // request's captured `s` no longer matches the live state.
      if (window.__mpChartState !== s) return;
      if (s.ticker !== tickerAtRequest) return;  // ticker switch (paranoid)
      if (!chunk.bars || chunk.bars.length === 0) {
        s.hasMoreHistory = false;
        return;
      }
      prependChunk(chunk);
      s.oldestLoaded = chunk.bars[0].time;
      // No rAF wait needed: setData on a non-sticky chart (initial render
      // used setVisibleLogicalRange instead of fitContent) preserves the
      // visible TIME range, so range.from in logical-index space grows by
      // the chunk size and naturally drops below the 60-bar trigger
      // threshold. No feedback loop possible.
    } catch (exc) {
      console.warn("lazy-load failed:", exc);
    } finally {
      s.loadingMore = false;
      showLoadingDot(false);
    }
  }
```

Replace with:

```javascript
  async function loadMoreHistory() {
    const s = window.__mpChartState;
    if (!s || s.loadingMore || !s.hasMoreHistory || !s.ticker) return;
    s.loadingMore = true;
    const tickerAtRequest = s.ticker;
    // Capture visible range BEFORE the fetch — after prependChunk the
    // chart's logical indices shift and we need the pre-prepend values
    // to compute the shifted target range.
    const prevRange = s.mainChart.timeScale().getVisibleLogicalRange();
    console.debug(
      "mp-chart loadMore →",
      { ticker: s.ticker, oldestLoaded: s.oldestLoaded, prevRange },
    );
    showLoadingDot(true);
    try {
      const r = await fetch(
        `/stock/${s.ticker}/chart-data?before=${s.oldestLoaded}&count=180`,
      );
      if (!r.ok) return;  // log+retry on next scroll
      const chunk = await r.json();
      // State-object identity check catches period switches too — period
      // switch creates a new state object via freshState(), so an in-flight
      // request's captured `s` no longer matches the live state.
      if (window.__mpChartState !== s) return;
      if (s.ticker !== tickerAtRequest) return;  // ticker switch (paranoid)
      if (!chunk.bars || chunk.bars.length === 0) {
        s.hasMoreHistory = false;
        return;
      }
      prependChunk(chunk);
      s.oldestLoaded = chunk.bars[0].time;
      // Explicit view shift: prependChunk's setData calls cause
      // lightweight-charts to refit (often jumping to the start of the
      // expanded dataset). Shifting the visible logical range right by
      // chunk.bars.length keeps the user anchored on the same time
      // window. Without this, barsBefore drops below threshold again
      // immediately after the load and we cascade.
      if (prevRange) {
        const newRange = {
          from: prevRange.from + chunk.bars.length,
          to: prevRange.to + chunk.bars.length,
        };
        s.mainChart.timeScale().setVisibleLogicalRange(newRange);
        console.debug(
          "mp-chart loadMore ✓",
          { chunkLen: chunk.bars.length, prevRange, newRange,
            barsTotal: s.bars.length },
        );
      }
    } catch (exc) {
      console.warn("lazy-load failed:", exc);
    } finally {
      s.loadingMore = false;
      showLoadingDot(false);
    }
  }
```

- [ ] **Step 2b: Verify backend tests still pass**

Run: `uv run pytest 2>&1 | tail -3`
Expected: `293 passed`.

- [ ] **Step 2c: Commit**

```bash
git add marketpulse/web/static/chart.js
git commit -m "$(cat <<'EOF'
fix(chart): explicitly shift visible range after lazy-load prepend

prependChunk's setData calls cause lightweight-charts to refit — empirical
test showed visibleTimeRange jumping to 2016-06-06..2016-06-07 (the oldest
end of the dataset) after a prepend, not the original time window the user
was looking at. Without explicit recovery, barsBefore drops back below
threshold immediately and the cascade resumes.

Fix: capture prevRange before the fetch, then after prepend land call
setVisibleLogicalRange with {from: prevRange.from + chunkLen, to:
prevRange.to + chunkLen}. The user's view stays on the same calendar
dates. barsBefore grows by chunkLen and rises above threshold.

Adds two console.debug lines (mp-chart loadMore → / ✓) so prod behavior
is observable in DevTools without a monitoring stack.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Anchor initial view to last 60 bars + clean stale comments

Replace the current `setVisibleLogicalRange({from: 0, to: s.bars.length})` initial-view block with the 60-bar anchor. Remove the now-misleading comment blocks ("No setVisibleLogicalRange needed" in `prependChunk`).

**Files:**
- Modify: `marketpulse/web/static/chart.js`

- [ ] **Step 3a: Edit the initial-view block in `renderCharts`**

Find this block (currently just before the subscription handler from Task 1):

```javascript
    // Anchor initial view to the most recent ~60 bars. If we show ALL
    // initial bars, range.from is 0 from the very first paint and the
    // lazy-load subscription fires immediately — cascading until history
    // runs out. Showing the last 60 puts range.from well above the
    // 60-bar trigger threshold, so loads only happen when the user
    // actually scrolls left.
    const initialFrom = Math.max(0, s.bars.length - 60);
    s.mainChart.timeScale().setVisibleLogicalRange({
      from: initialFrom,
      to: s.bars.length,
    });
```

Wait — the current main has a different version. Verify by running:

```bash
sed -n '188,202p' marketpulse/web/static/chart.js
```

If the current block matches what's in Task 3a's "find" snippet above, leave it as-is and skip to Step 3b. If it differs (e.g., uses `{from: 0, to: s.bars.length}` or any other variant), replace with this exact block:

```javascript
    // Anchor initial view to the most recent ~60 bars. The lazy-load
    // subscription uses barsInLogicalRange(range).barsBefore < 50 — if
    // we showed all initial bars at first paint, barsBefore would be 0
    // and a fetch would fire immediately. Anchoring to 60 means the
    // prefetch only happens if the initial dataset is shorter than 60
    // bars (i.e., a very-newly-listed ticker).
    s.mainChart.timeScale().setVisibleLogicalRange({
      from: Math.max(0, s.bars.length - 60),
      to: s.bars.length,
    });
```

- [ ] **Step 3b: Remove the stale comment block in `prependChunk`**

Find and delete this block at the end of `prependChunk` (just before the closing `}`):

```javascript
    // No setVisibleLogicalRange needed: because the chart is NOT in
    // sticky fitContent mode (see renderCharts), setData preserves the
    // visible TIME range automatically. The user's view stays anchored
    // on the same bars and range.from grows by chunk.bars.length in
    // logical-index space.
```

The setVisibleLogicalRange call now lives in `loadMoreHistory` (Task 2), so this comment is both wrong (setData does NOT preserve time range — see PR #18 evidence) and stale.

- [ ] **Step 3c: Verify backend tests still pass**

Run: `uv run pytest 2>&1 | tail -3`
Expected: `293 passed`.

- [ ] **Step 3d: Verify no other stale comments slip through**

Run: `grep -n "preserves the visible TIME range\|fitContent\|sticky" marketpulse/web/static/chart.js`
Expected: no matches (all comments that referenced the obsolete time-range-preservation theory should be gone).

If the grep returns lines, read them in context and remove if they're stale. Acceptable to keep references that talk about *why we're NOT using fitContent* if any remain — but the simpler path is to remove them entirely since the spec already documents the rationale.

- [ ] **Step 3e: Commit**

```bash
git add marketpulse/web/static/chart.js
git commit -m "$(cat <<'EOF'
chore(chart): anchor initial view to last 60 bars, drop stale comments

Initial render now uses setVisibleLogicalRange({from: bars.length-60,
to: bars.length}) so the chart opens on recent prices and barsBefore
starts at >50 (no immediate fetch). The prefetch-on-load only triggers
when the initial dataset is shorter than 60 bars (very-new tickers).

Drops three stale comment blocks that referenced the now-disproven
"setData preserves time range" theory from PR #18.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Push and open PR for manual verification

Frontend has no test harness, so the only way to validate is by deploying and watching DevTools. Push the branch, open a PR with a precise manual test plan, wait for deploy.

- [ ] **Step 4a: Push the branch**

Run: `git push -u origin fix/chart-lazy-load-tradingview-pattern`
Expected: branch published, no errors.

- [ ] **Step 4b: Open the PR**

Run:

```bash
gh pr create --title "fix(chart): rewrite lazy-load using TradingView barsBefore pattern" --body "$(cat <<'EOF'
## Summary

Six prior PRs (#10, #12–#18) chased the same root cause: the lazy-load trigger used `range.from < 60`, whose semantic meaning drifts after every `setData()` prepend. This PR replaces the entire pattern with TradingView's official `barsInLogicalRange(range).barsBefore` metric, which is dataset-stable across prepends.

Three focused commits:
1. **Trigger**: `subscribeVisibleLogicalRangeChange` uses `info.barsBefore < 50` instead of `range.from < 60`.
2. **View shift**: `loadMoreHistory` captures `prevRange` before fetch and explicitly calls `setVisibleLogicalRange({from: prevRange.from + chunkLen, to: prevRange.to + chunkLen})` after prepend. Adds two `console.debug` lines (`mp-chart loadMore →` / `✓`) for in-production observability.
3. **Initial anchor**: `renderCharts` anchors to last 60 bars so first paint has `barsBefore > 50` and no immediate fetch fires.

Spec: \`docs/superpowers/specs/2026-05-12-chart-lazy-load-v2-design.md\`

## Why this is different from PRs #14/#15/#17/#18

| Past attempt | Why it failed |
|---|---|
| #14 capture+restore range | trigger metric (\`range.from\`) still drifted |
| #15 rAF wait | patched a symptom of \`fitContent\` sticky mode |
| #17 \`setVisibleLogicalRange\` instead of \`fitContent\` | anchored a logical range whose meaning shifts on prepend |
| #18 trust \`setData\` to preserve time range | empirically false — chart still refits |
| v2 (this PR) | \`barsBefore\` is invariant to prepend by construction + explicit shift means no reliance on chart auto-fit |

## Test Plan

Backend tests:
- [x] 293/293 pass (no backend changes)
- [x] \`ruff check\` clean

Manual after deploy (devtools open, Network filter \`chart-data\`):
- [ ] Open /stock/QUBT. Expect: 1 \`?period=60d\` request, no \`?before=\` cascade. Chart shows ~60 recent bars.
- [ ] Console shows nothing under \`mp-chart\` filter (no lazy-load triggered yet).
- [ ] Mouse-wheel scroll left until you've passed ~10 bars. Expect: \`mp-chart loadMore →\` then \`mp-chart loadMore ✓\` in console, 1 new \`?before=\` request, older bars appear, visible window stays on the same calendar dates.
- [ ] Keep scrolling. Each cascade should be triggered by user motion, not auto-fire. Eventually \`hasMoreHistory: false\` (around 2018 for QUBT) and no more requests.
- [ ] Click 30D button. State resets, no leaked fetches.
- [ ] Resize browser window. Bars stretch (autoSize), no flicker, no extra fetches.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed to stdout.

- [ ] **Step 4c: Report PR URL**

Print the PR URL so the operator can deploy and run the manual test plan.

---

## Self-Review Checklist (for the engineer running this plan)

Before declaring "done":

- [ ] All 4 task commits exist on the branch (`git log --oneline | head -5`)
- [ ] `293 passed` on the final commit (`uv run pytest 2>&1 | tail -3`)
- [ ] `ruff check` clean (`uv run ruff check 2>&1 | tail -3`)
- [ ] No grep hits for `range\.from < 60` in chart.js (`grep -n 'range\.from' marketpulse/web/static/chart.js` should show no `< 60` comparisons)
- [ ] No grep hits for `fitContent` in chart.js (`grep -n fitContent marketpulse/web/static/chart.js` should be empty)
- [ ] `barsInLogicalRange` appears exactly once in chart.js (`grep -n barsInLogicalRange marketpulse/web/static/chart.js | wc -l` → `1`)
- [ ] `setVisibleLogicalRange` appears exactly twice in chart.js (`grep -n setVisibleLogicalRange marketpulse/web/static/chart.js | wc -l` → `2` — one in `renderCharts`, one in `loadMoreHistory`)
- [ ] PR opened and URL reported

If any check fails, do not declare done — investigate and fix.
