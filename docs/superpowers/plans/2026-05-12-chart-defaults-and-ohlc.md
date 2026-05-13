# Chart Defaults + Period Range + OHLC Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** 1Y default + localStorage memory, YTD/5Y/All period buttons (drop 30D), top OHLC bar synced to crosshair.

**Architecture:** Three independent commits, all in `chart.js` / `stock.html` / `routes/stock.py`. Tests extended in `test_stock.py`.

**Spec:** [`docs/superpowers/specs/2026-05-12-chart-defaults-and-ohlc-design.md`](../specs/2026-05-12-chart-defaults-and-ohlc-design.md)

**Branch:** `fix/chart-defaults-and-ohlc` off `main`.

---

## Pre-flight

- [ ] **Step 0a:** `git branch --show-current` → `fix/chart-defaults-and-ohlc`; `git status --short` → empty; `uv run pytest 2>&1 | tail -3` → 293 passed.

---

## Task 1: Backend — extend period set + YTD/5Y/All handling

**Files:** `marketpulse/web/routes/stock.py`, `tests/web/test_stock.py`

- [ ] **Step 1a: Add failing tests**

Append to `tests/web/test_stock.py`:

```python
def test_chart_data_ytd_returns_year_to_date(client: TestClient, monkeypatch):
    from datetime import date
    _login(client, monkeypatch)
    r = client.get("/stock/AAPL/chart-data?period=ytd")
    assert r.status_code == 200
    payload = r.json()
    if not payload["bars"]:
        return  # no data this year yet, acceptable
    first_bar_date = date.fromisoformat(payload["bars"][0]["time"])
    today = date.today()
    assert first_bar_date >= date(today.year, 1, 1)
    assert first_bar_date <= today


def test_chart_data_5y_uses_yfinance(client: TestClient, monkeypatch):
    from datetime import date, timedelta
    from marketpulse.data.yfinance_client import YFinanceClient
    _login(client, monkeypatch)
    called_with = {}
    def fake_fetch_range(self, ticker, *, start, end):
        called_with["ticker"] = ticker
        called_with["start"] = start
        called_with["end"] = end
        return []
    monkeypatch.setattr(YFinanceClient, "fetch_history_range", fake_fetch_range)
    r = client.get("/stock/AAPL/chart-data?period=5y")
    assert r.status_code == 200
    assert called_with["ticker"] == "AAPL"
    expected_start = date.today() - timedelta(days=1825)
    # Allow a 2-day tolerance for calendar weirdness
    assert abs((called_with["start"] - expected_start).days) <= 2
    assert called_with["end"] == date.today()


def test_chart_data_all_uses_yfinance_from_1900(client: TestClient, monkeypatch):
    from datetime import date
    from marketpulse.data.yfinance_client import YFinanceClient
    _login(client, monkeypatch)
    called_with = {}
    def fake_fetch_range(self, ticker, *, start, end):
        called_with["start"] = start
        return []
    monkeypatch.setattr(YFinanceClient, "fetch_history_range", fake_fetch_range)
    r = client.get("/stock/AAPL/chart-data?period=all")
    assert r.status_code == 200
    assert called_with["start"] <= date(1900, 1, 1)


def test_chart_data_rejects_30d(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/stock/AAPL/chart-data?period=30d")
    assert r.status_code == 422


def test_chart_data_rejects_invalid_period(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/stock/AAPL/chart-data?period=foo")
    assert r.status_code == 422
```

Note: `test_stock.py` may not already have a `_login` helper. If it doesn't, copy the pattern from `test_trades.py`:

```python
from marketpulse.auth.password import hash_password

def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})
```

Check existing test_stock.py first; many tests there may use a different auth bypass. If they use `monkeypatch.setattr("marketpulse.web.routes.stock.require_auth", lambda: None)`, use the same pattern instead of `_login`.

- [ ] **Step 1b: Run, confirm fails**

```
uv run pytest tests/web/test_stock.py -k "ytd or 5y or all_uses_yfinance or rejects_30d or rejects_invalid" -v
```
Expected: 5 FAIL (the route currently doesn't accept these periods).

- [ ] **Step 1c: Update the route**

In `marketpulse/web/routes/stock.py`:

1. Update constants near the top:

```python
_VALID_PERIODS = {"60d", "6m", "ytd", "1y", "5y", "all"}
_PERIOD_DAYS_FIXED = {"60d": 60, "6m": 180, "1y": 365, "5y": 1825}
```

(remove the old `_PERIOD_DAYS` and "30d" key)

2. In `stock_chart_data`, after the `if before is not None` block but before the `_VALID_PERIODS` check, the period validation stays:

```python
    if period not in _VALID_PERIODS:
        raise HTTPException(
            status_code=422,
            detail=f"period must be one of {sorted(_VALID_PERIODS)}",
        )
```

3. Replace the existing "existing period code path" block with branched logic:

```python
    # Short periods (≤ 1y) use the fast Tencent path with a 1y cache. Long
    # periods (5y, All) fall through to yfinance which can return arbitrary
    # date ranges — slower but the only way to cover multi-year history.
    if period in {"5y", "all"}:
        from marketpulse.data.yfinance_client import YFinanceClient
        if period == "5y":
            start = date.today() - timedelta(days=_PERIOD_DAYS_FIXED["5y"])
        else:  # "all"
            start = date(1900, 1, 1)
        try:
            all_bars = YFinanceClient().fetch_history_range(
                ticker, start=start, end=date.today(),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "chart_data_long_period_failed", ticker=ticker,
                period=period, error=str(exc),
            )
            all_bars = []
        if not all_bars:
            return JSONResponse(
                _empty_payload(),
                headers={"Cache-Control": "private, max-age=300"},
            )
        return JSONResponse(
            _build_payload(all_bars, cutoff=all_bars[0].date),
            headers={"Cache-Control": "private, max-age=300"},
        )

    # Short periods: Tencent fast path (existing behavior).
    try:
        all_bars = data.get_history(ticker, period="1y")
    except Exception as exc:
        log.warning("chart_data_history_failed", ticker=ticker, error=str(exc))
        all_bars = []

    if period == "ytd":
        cutoff = date(date.today().year, 1, 1)
    else:
        cutoff = date.today() - timedelta(days=_PERIOD_DAYS_FIXED[period])

    if not all_bars:
        return JSONResponse(
            _empty_payload(), headers={"Cache-Control": "private, max-age=300"},
        )

    return JSONResponse(
        _build_payload(all_bars, cutoff=cutoff),
        headers={"Cache-Control": "private, max-age=300"},
    )
```

- [ ] **Step 1d: Verify**

```
uv run pytest tests/web/test_stock.py -k "ytd or 5y or all_uses_yfinance or rejects_30d or rejects_invalid" -v
uv run pytest tests/web/test_stock.py 2>&1 | tail -5
uv run pytest 2>&1 | tail -3
uv run ruff check 2>&1 | tail -3
```
Expected: 5 new PASS, all green.

- [ ] **Step 1e: Commit**

```bash
git add marketpulse/web/routes/stock.py tests/web/test_stock.py
git commit -m "$(cat <<'EOF'
feat(chart): add YTD/5Y/All periods, drop 30D

Period set goes from {30d, 60d, 6m, 1y} to {60d, 6m, ytd, 1y, 5y, all}.

YTD computes its cutoff as Jan 1 of the current calendar year. 5Y and
All bypass the Tencent fast path (capped at ~1y) and use yfinance with
explicit start/end dates. 30D is dropped — low value next to 60D.

The Tencent path stays the default for ≤1y periods (faster, China-
friendly). yfinance is opt-in via the explicit long periods.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Frontend — period buttons + localStorage memory

**Files:** `marketpulse/web/templates/stock.html`, `marketpulse/web/static/chart.js`, `tests/web/test_stock.py`

- [ ] **Step 2a: Failing test (template assertion)**

Append to `tests/web/test_stock.py`:

```python
def test_stock_page_has_new_period_buttons(client: TestClient, monkeypatch):
    """The /stock/{ticker} page must show YTD/5Y/All buttons and no 30D button."""
    _login(client, monkeypatch)
    # Create a minimal ticker context — page should still render even if API mocks return empty
    r = client.get("/stock/AAPL")
    assert r.status_code == 200
    body = r.text
    assert 'data-period="ytd"' in body, "YTD button missing"
    assert 'data-period="5y"' in body, "5Y button missing"
    assert 'data-period="all"' in body, "All button missing"
    assert 'data-period="30d"' not in body, "30D button must be removed"


def test_stock_page_has_ohlc_bar(client: TestClient, monkeypatch):
    """Chart page must include an OHLC bar element above the chart."""
    _login(client, monkeypatch)
    r = client.get("/stock/AAPL")
    assert r.status_code == 200
    body = r.text
    assert 'id="chart-ohlc-bar"' in body
    assert 'data-ohlc="open"' in body
    assert 'data-ohlc="high"' in body
    assert 'data-ohlc="low"' in body
    assert 'data-ohlc="close"' in body
    assert 'data-ohlc="change"' in body


def test_chart_js_uses_localstorage_for_period(client: TestClient, monkeypatch):
    """chart.js must reference localStorage for period persistence."""
    _login(client, monkeypatch)
    r = client.get("/static/chart.js")
    assert r.status_code == 200
    body = r.text
    assert "localStorage" in body, "chart.js must persist period across sessions"
    assert "mp.chartPeriod" in body, "chart.js must use the agreed storage key"
```

If `test_stock.py` test_stock_page_has_new_period_buttons (or similar) already asserts the OLD button set, locate it and update to match the new list — drop the 30D assertion, add YTD/5Y/All.

- [ ] **Step 2b: Run, confirm fails**

```
uv run pytest tests/web/test_stock.py -k "new_period_buttons or ohlc_bar or localstorage_for_period" -v
```

- [ ] **Step 2c: Update the template `stock.html`**

Find the existing period buttons block:

```html
    <button data-period="30d" class="px-2 py-1 rounded border border-slate-200">30D</button>
    <button data-period="60d" class="px-2 py-1 rounded border border-slate-200 bg-slate-900 text-white">60D</button>
    <button data-period="6m"  class="px-2 py-1 rounded border border-slate-200">6M</button>
    <button data-period="1y"  class="px-2 py-1 rounded border border-slate-200">1Y</button>
```

Replace with (note: no hardcoded active style — JS applies based on localStorage):

```html
    <button data-period="60d" class="px-2 py-1 rounded border border-slate-200">60D</button>
    <button data-period="6m"  class="px-2 py-1 rounded border border-slate-200">6M</button>
    <button data-period="ytd" class="px-2 py-1 rounded border border-slate-200">YTD</button>
    <button data-period="1y"  class="px-2 py-1 rounded border border-slate-200">1Y</button>
    <button data-period="5y"  class="px-2 py-1 rounded border border-slate-200">5Y</button>
    <button data-period="all" class="px-2 py-1 rounded border border-slate-200">All</button>
```

Just above the `<div id="chart-main" ...>` element (the chart container), add the OHLC bar:

```html
  <div id="chart-ohlc-bar" class="text-xs flex gap-3 text-slate-600 mb-1 font-mono">
    <span><span class="text-slate-400">O</span> <span data-ohlc="open">—</span></span>
    <span><span class="text-slate-400">H</span> <span data-ohlc="high">—</span></span>
    <span><span class="text-slate-400">L</span> <span data-ohlc="low">—</span></span>
    <span><span class="text-slate-400">C</span> <span data-ohlc="close">—</span></span>
    <span data-ohlc="change" class="font-semibold"></span>
  </div>
```

- [ ] **Step 2d: Update `chart.js`**

Find the DOMContentLoaded handler at the bottom. Current:

```javascript
  document.addEventListener("DOMContentLoaded", () => {
    const main = document.getElementById("chart-main");
    if (!main) return;
    const ticker = main.dataset.ticker;
    let currentPeriod = "60d";
    load(ticker, currentPeriod);

    document.querySelectorAll("[data-period]").forEach(btn => {
      btn.addEventListener("click", () => {
        currentPeriod = btn.dataset.period;
        document.querySelectorAll("[data-period]").forEach(b => {
          const active = b === btn;
          b.classList.toggle("bg-slate-900", active);
          b.classList.toggle("text-white", active);
        });
        load(ticker, currentPeriod);
      });
    });

    document.getElementById("toggle-bb")?.addEventListener("change", applyToggles);
    document.getElementById("toggle-sma")?.addEventListener("change", applyToggles);
  });
```

Replace with:

```javascript
  const PERIOD_STORAGE_KEY = "mp.chartPeriod";
  const VALID_STORED_PERIODS = new Set(["60d", "6m", "ytd", "1y", "5y", "all"]);

  function readStoredPeriod() {
    try {
      const v = localStorage.getItem(PERIOD_STORAGE_KEY);
      return VALID_STORED_PERIODS.has(v) ? v : "1y";
    } catch {
      return "1y";  // localStorage may throw in private mode
    }
  }

  function writeStoredPeriod(p) {
    try {
      if (VALID_STORED_PERIODS.has(p)) {
        localStorage.setItem(PERIOD_STORAGE_KEY, p);
      }
    } catch {
      // ignore — quota or disabled
    }
  }

  function applyActiveButton(period) {
    document.querySelectorAll("[data-period]").forEach(b => {
      const active = b.dataset.period === period;
      b.classList.toggle("bg-slate-900", active);
      b.classList.toggle("text-white", active);
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const main = document.getElementById("chart-main");
    if (!main) return;
    const ticker = main.dataset.ticker;
    let currentPeriod = readStoredPeriod();
    applyActiveButton(currentPeriod);
    load(ticker, currentPeriod);

    document.querySelectorAll("[data-period]").forEach(btn => {
      btn.addEventListener("click", () => {
        currentPeriod = btn.dataset.period;
        writeStoredPeriod(currentPeriod);
        applyActiveButton(currentPeriod);
        load(ticker, currentPeriod);
      });
    });

    document.getElementById("toggle-bb")?.addEventListener("change", applyToggles);
    document.getElementById("toggle-sma")?.addEventListener("change", applyToggles);
  });
```

- [ ] **Step 2e: Verify**

```
uv run pytest tests/web/test_stock.py -k "new_period_buttons or ohlc_bar or localstorage_for_period" -v
uv run pytest tests/web/test_stock.py 2>&1 | tail -5
uv run pytest 2>&1 | tail -3
uv run ruff check 2>&1 | tail -3
```
Expected: all green.

- [ ] **Step 2f: Commit**

```bash
git add marketpulse/web/templates/stock.html marketpulse/web/static/chart.js tests/web/test_stock.py
git commit -m "$(cat <<'EOF'
feat(chart): default 1Y + localStorage memory for period choice

The period buttons now persist via localStorage (key "mp.chartPeriod").
On page load, the chart opens with the last-chosen period, defaulting
to 1Y on first visit (changed from 60D).

Per spec: 60D-as-default made the chart feel like a snapshot; users
typically switched to 1Y as their first action. 1Y is the new default,
and the choice sticks.

Wraps localStorage access in try/catch so private-browsing failures
degrade gracefully to the default.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Note this commit also adds the new period buttons (YTD/5Y/All) and the OHLC bar HTML element. Task 3 wires up the OHLC bar to crosshair events.

---

## Task 3: Wire the OHLC bar to crosshair

**Files:** `marketpulse/web/static/chart.js`, `tests/web/test_stock.py`

- [ ] **Step 3a: Failing test**

Append to `tests/web/test_stock.py`:

```python
def test_chart_js_subscribes_crosshair_for_ohlc(client: TestClient, monkeypatch):
    """chart.js must subscribe to crosshair moves and update the OHLC bar."""
    _login(client, monkeypatch)
    r = client.get("/static/chart.js")
    assert r.status_code == 200
    body = r.text
    assert "subscribeCrosshairMove" in body, (
        "chart.js must subscribe to crosshair to keep OHLC bar in sync"
    )
    assert "updateOhlcBar" in body, "updateOhlcBar function missing"
    assert 'data-ohlc="open"' in body or "[data-ohlc=" in body, (
        "updateOhlcBar must select OHLC field elements"
    )
```

- [ ] **Step 3b: Run, confirm fails**

```
uv run pytest tests/web/test_stock.py::test_chart_js_subscribes_crosshair_for_ohlc -v
```

- [ ] **Step 3c: Add `updateOhlcBar` + crosshair subscription**

In `marketpulse/web/static/chart.js`, just before the `densify` (or `withWhitespace`) function definition (top of the IIFE, after SIGNAL_STYLES and freshState), add:

```javascript
  function updateOhlcBar(bar) {
    const el = document.getElementById("chart-ohlc-bar");
    if (!el || !bar) return;
    const open  = bar.open ?? bar.value;   // candle vs line bars
    const high  = bar.high ?? bar.value;
    const low   = bar.low  ?? bar.value;
    const close = bar.close ?? bar.value;
    if (open == null || close == null) return;
    el.querySelector('[data-ohlc="open"]').textContent  = open.toFixed(2);
    el.querySelector('[data-ohlc="high"]').textContent  = high.toFixed(2);
    el.querySelector('[data-ohlc="low"]').textContent   = low.toFixed(2);
    el.querySelector('[data-ohlc="close"]').textContent = close.toFixed(2);
    const change = close - open;
    const pct = open !== 0 ? (change / open) * 100 : 0;
    const changeEl = el.querySelector('[data-ohlc="change"]');
    const sign = change >= 0 ? "+" : "";
    changeEl.textContent = `${sign}${change.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
    changeEl.className = "font-semibold " + (change >= 0 ? "text-green-600" : "text-red-600");
  }
```

In `renderCharts`, find the place just AFTER `s.candleSeries.setData(s.bars);`. Add:

```javascript
    // Initial OHLC bar: show the latest bar.
    if (s.bars.length > 0) updateOhlcBar(s.bars[s.bars.length - 1]);
```

Then find the existing `subscribeVisibleLogicalRangeChange` block (the lazy-load trigger). Just BEFORE it, add:

```javascript
    // Keep the top OHLC bar synced to the crosshair position.
    // On hover: show the bar under the cursor. On no-hover: show latest.
    s.mainChart.subscribeCrosshairMove(param => {
      const bar = param.seriesData && param.seriesData.get
        ? param.seriesData.get(s.candleSeries)
        : null;
      if (bar) {
        updateOhlcBar(bar);
      } else if (s.bars.length > 0) {
        updateOhlcBar(s.bars[s.bars.length - 1]);
      }
    });
```

Also, in `loadMoreHistory` (or in `prependChunk`), AFTER the prepend completes successfully, refresh the OHLC bar with the latest bar so it stays correct after lazy-load:

Find this section near the end of `loadMoreHistory`:

```javascript
      prependChunk(chunk);
      s.oldestLoaded = chunk.bars[0].time;
```

Add immediately after:

```javascript
      // Refresh the OHLC bar in case the user wasn't hovering — keeps the
      // displayed bar consistent with state.bars.
      if (s.bars.length > 0) updateOhlcBar(s.bars[s.bars.length - 1]);
```

- [ ] **Step 3d: Verify**

```
uv run pytest tests/web/test_stock.py::test_chart_js_subscribes_crosshair_for_ohlc -v
uv run pytest tests/web/test_stock.py 2>&1 | tail -5
uv run pytest 2>&1 | tail -3
uv run ruff check 2>&1 | tail -3
```
Expected: target test PASS, all green.

- [ ] **Step 3e: Commit**

```bash
git add marketpulse/web/static/chart.js tests/web/test_stock.py
git commit -m "$(cat <<'EOF'
feat(chart): top OHLC bar tracks crosshair (TradingView pattern)

A monospace OHLC + change% strip appears above the chart. On hover, it
shows the bar under the crosshair; off-hover it shows the latest bar.
Change is color-coded green/red.

Wires lightweight-charts' subscribeCrosshairMove to read the bar from
param.seriesData. Falls back to latest bar when crosshair is off-chart.
After every lazy-load prepend, the bar is refreshed so it stays in sync
with the loaded data.

This is the single biggest "I can read this chart" affordance we were
missing vs tradingview.com.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Push and open PR

- [ ] **Step 4a: Push**

```
git push -u origin fix/chart-defaults-and-ohlc
```

- [ ] **Step 4b: Open PR**

```bash
gh pr create --title "feat(chart): 1Y default, YTD/5Y/All periods, top OHLC bar" --body "$(cat <<'EOF'
## Summary

Three improvements after side-by-side comparison with tradingview.com:

1. **Default period 1Y + localStorage memory** — Open chart shows 1 year (was 60 days). Last-chosen period persists across sessions via \`localStorage["mp.chartPeriod"]\`. Private browsing degrades gracefully.

2. **More period buttons** — Drop 30D (low value next to 60D), add YTD, 5Y, All. Buttons: \`60D / 6M / YTD / 1Y / 5Y / All\`. Long periods (5Y, All) route through yfinance; short periods stay on the Tencent fast path.

3. **Top OHLC bar** — Above-chart strip with O/H/L/C + change%, color-coded green/red, synced to crosshair via \`subscribeCrosshairMove\`. Falls back to latest bar when crosshair is off-chart. Biggest "I can read this chart" affordance we were missing.

Spec: \`docs/superpowers/specs/2026-05-12-chart-defaults-and-ohlc-design.md\`

## Breaking change

\`?period=30d\` now returns 422. No external permalink contract was promised, and 30D was a redundant button next to 60D.

## Test Plan

- [x] 9 new tests pass (5 backend period tests + 4 frontend template/JS assertions)
- [x] \`ruff check\` clean
- [ ] Manual after deploy:
  - [ ] Open /stock/AAPL → 1Y by default
  - [ ] Click 5Y → loads ~1250 bars via yfinance (~3-5s with loading dot). Reload → still 5Y.
  - [ ] Click All on AAPL → multi-decade history loads
  - [ ] Click YTD → current calendar year only
  - [ ] Hover any bar → OHLC bar updates with that bar's values + change. Move off → latest bar. Change is green if up, red if down.
  - [ ] Old bookmark with \`?period=30d\` returns 422 (acceptable breakage)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4c: Self-review**

Run and report:
- `git log --oneline | head -5` — 3 commits ahead of main
- `uv run pytest 2>&1 | tail -3` — all green
- `uv run ruff check 2>&1 | tail -3` — clean
- `grep -c 'data-period="ytd"\|data-period="5y"\|data-period="all"' marketpulse/web/templates/stock.html` — exactly 3
- `grep -c 'data-period="30d"' marketpulse/web/templates/stock.html` — exactly 0
- `grep -c localStorage marketpulse/web/static/chart.js` — at least 2
- `grep -c subscribeCrosshairMove marketpulse/web/static/chart.js` — at least 1
- `grep -c updateOhlcBar marketpulse/web/static/chart.js` — at least 3 (definition + crosshair handler + lazy-load refresh + initial call)

Report PR URL.
