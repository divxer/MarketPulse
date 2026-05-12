# K-Line Chart Lazy-Load Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mimic TradingView's infinite-scroll behavior — when the user mouse-wheel scrolls toward the left edge of the K-line chart, automatically fetch earlier historical data via yfinance and prepend it to the visible series without jumping the scroll position.

**Architecture:** Extend the existing `/stock/{ticker}/chart-data` endpoint with `before` + `count` parameters that take a yfinance path with 250-day indicator lookback padding. Frontend maintains module-level state in `window.__mpChartState`, subscribes to `visibleLogicalRangeChange`, and when leftmost visible bar approaches loaded leftmost bar, triggers a debounced fetch. New bars/indicators prepend to in-memory state and `series.setData(combined)` is called on each handle — lightweight-charts preserves the visible time range across `setData`, so scroll position doesn't jump.

**Tech Stack:** Python 3.12, FastAPI, yfinance, TradingView lightweight-charts v4, vanilla JS, pytest. Spec at `docs/superpowers/specs/2026-05-12-chart-lazy-load-design.md`.

---

## File Structure

**New files:**

| File | Responsibility |
|---|---|
| `tests/unit/test_yfinance_history_range.py` | Unit test for new `YFinanceClient.fetch_history_range` |
| `tests/integration/test_chart_data_lazy.py` | End-to-end test: initial `?period=60d` then `?before=…&count=180` |

**Modified files:**

| File | Change |
|---|---|
| `marketpulse/data/yfinance_client.py` | Add `fetch_history_range(ticker, start, end)` |
| `marketpulse/web/routes/stock.py` | Extend `stock_chart_data` to accept `before` + `count`; lazy-load path that fetches with 250-day lookback, computes indicators, trims by date |
| `marketpulse/web/static/chart.js` | `window.__mpChartState`; subscribe `visibleLogicalRangeChange`; `loadMoreHistory()`; `prependChunk()`; loading-dot CSS |
| `marketpulse/web/templates/stock.html` | Add `<div id="chart-loading-dot">` overlay; make chart-main wrapper `relative` |
| `tests/web/test_stock.py` | New tests for `before`/`count` behavior |

---

## Task 1: `YFinanceClient.fetch_history_range`

**Files:**
- Modify: `marketpulse/data/yfinance_client.py`
- Create: `tests/unit/test_yfinance_history_range.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_yfinance_history_range.py`:

```python
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd


def _hist_df(rows: list[tuple[str, float, float, float, float, int]]) -> pd.DataFrame:
    """Build a yfinance-shaped DataFrame from (date_str, o, h, l, c, vol) tuples."""
    df = pd.DataFrame(
        rows, columns=["date", "Open", "High", "Low", "Close", "Volume"],
    )
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def test_fetch_history_range_returns_bars_in_window() -> None:
    from marketpulse.data.yfinance_client import YFinanceClient

    fake_ticker = MagicMock()
    fake_ticker.history.return_value = _hist_df([
        ("2024-01-02", 100.0, 102.0, 99.5, 101.5, 1_000_000),
        ("2024-01-03", 101.5, 103.0, 101.0, 102.5, 1_100_000),
        ("2024-01-04", 102.5, 102.8, 100.5, 101.0, 900_000),
    ])

    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        bars = YFinanceClient().fetch_history_range(
            "AAPL", start=date(2024, 1, 2), end=date(2024, 1, 4),
        )

    # yf.Ticker.history called with the right start/end + daily interval
    fake_ticker.history.assert_called_once()
    kwargs = fake_ticker.history.call_args.kwargs
    assert kwargs["start"] == date(2024, 1, 2)
    assert kwargs["end"] == date(2024, 1, 4)
    assert kwargs["interval"] == "1d"

    assert len(bars) == 3
    assert bars[0].date == date(2024, 1, 2)
    assert bars[0].close == 101.5
    assert bars[2].date == date(2024, 1, 4)


def test_fetch_history_range_empty_returns_empty_list() -> None:
    from marketpulse.data.yfinance_client import YFinanceClient

    fake_ticker = MagicMock()
    fake_ticker.history.return_value = pd.DataFrame(
        columns=["Open", "High", "Low", "Close", "Volume"],
    )

    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        bars = YFinanceClient().fetch_history_range(
            "NOSUCH", start=date(2020, 1, 1), end=date(2020, 1, 31),
        )

    assert bars == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
source .venv/bin/activate
python -m pytest tests/unit/test_yfinance_history_range.py -v
```

Expected: FAIL with `AttributeError: 'YFinanceClient' object has no attribute 'fetch_history_range'`.

- [ ] **Step 3: Implement the method**

In `marketpulse/data/yfinance_client.py`, add the method to `YFinanceClient`. Place it immediately after the existing `fetch_history` method:

```python
    @_retry
    def fetch_history_range(
        self, ticker: str, *, start: date, end: date,
    ) -> list[Bar]:
        """Daily OHLCV bars for an explicit [start, end] window via yfinance.

        Used by the chart-data endpoint's lazy-load path which needs to fetch
        an arbitrary historical slice (Tencent's Usfqkline only returns the
        latest N rows). Inclusive of both endpoints. Returns Bars sorted
        oldest-first; empty list if yfinance has no data in the window.
        """
        hist = yf.Ticker(ticker).history(start=start, end=end, interval="1d")
        bars: list[Bar] = []
        for idx, row in hist.iterrows():
            bars.append(
                Bar(
                    date=idx.date() if hasattr(idx, "date") else idx,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row["Volume"]),
                )
            )
        return bars
```

`Bar`, `_retry`, `yf`, and `date` are already imported at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_yfinance_history_range.py -v
```

Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/data/yfinance_client.py tests/unit/test_yfinance_history_range.py
git commit -m "feat(yfinance): fetch_history_range for arbitrary date windows"
```

---

## Task 2: Backend lazy-load route path

**Files:**
- Modify: `marketpulse/web/routes/stock.py`
- Test: `tests/web/test_stock.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_stock.py`:

```python
def test_chart_data_before_param_returns_window_before_date(
    client: TestClient, monkeypatch,
) -> None:
    """?before=2024-06-01&count=180 → bars dated strictly before 2024-06-01,
    at most 180 of them, indicators trimmed to the same window.
    """
    from datetime import date
    from unittest.mock import patch
    from marketpulse.data.types import Bar

    _login(client, monkeypatch)
    # Build a fake yfinance window: 430 bars (padding + chunk) ending 2024-05-31.
    # We don't need real OHLCV math — just unique close values + ascending dates.
    from datetime import timedelta as _td
    fake_bars = []
    base = date(2023, 3, 28)  # 430 trading days back is approximate; use 430 calendar
    for i in range(430):
        d = base + _td(days=i)
        fake_bars.append(Bar(date=d, open=10.0, high=10.5, low=9.5,
                             close=10.0 + (i * 0.01),
                             volume=1_000_000))

    with patch(
        "marketpulse.data.yfinance_client.YFinanceClient.fetch_history_range",
        return_value=fake_bars,
    ):
        res = client.get("/stock/AAPL/chart-data?before=2024-06-01&count=180")

    assert res.status_code == 200
    body = res.json()
    bars = body["bars"]
    assert len(bars) <= 180
    # Every returned bar must be strictly before the `before` cutoff.
    assert all(b["time"] < "2024-06-01" for b in bars)
    # Bars are oldest-first.
    times = [b["time"] for b in bars]
    assert times == sorted(times)


def test_chart_data_before_empty_when_no_data(client: TestClient, monkeypatch) -> None:
    """If yfinance returns no data in the window (ticker not yet IPO'd),
    response has bars=[] and all indicator arrays empty."""
    from unittest.mock import patch

    _login(client, monkeypatch)
    with patch(
        "marketpulse.data.yfinance_client.YFinanceClient.fetch_history_range",
        return_value=[],
    ):
        res = client.get("/stock/AAPL/chart-data?before=1900-01-01&count=180")

    assert res.status_code == 200
    body = res.json()
    assert body["bars"] == []
    assert body["sma200"] == []
    assert body["rsi"] == []


def test_chart_data_period_still_works(client: TestClient, monkeypatch) -> None:
    """Regression: the existing ?period=60d path is unchanged."""
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        res = client.get("/stock/AAPL/chart-data?period=60d")
        assert res.status_code == 200
        assert "bars" in res.json()
    finally:
        client.app.dependency_overrides.clear()


def test_chart_data_before_invalid_date_returns_422(
    client: TestClient, monkeypatch,
) -> None:
    _login(client, monkeypatch)
    res = client.get("/stock/AAPL/chart-data?before=not-a-date&count=180")
    assert res.status_code == 422


def test_chart_data_count_capped_at_max(client: TestClient, monkeypatch) -> None:
    """Sanity: count is bounded to prevent abuse (e.g., 1_000_000 days)."""
    _login(client, monkeypatch)
    res = client.get("/stock/AAPL/chart-data?before=2024-06-01&count=999999")
    # Either 422 with a clear message, or silently capped — either is acceptable;
    # this test asserts the API doesn't OOM trying to fulfill it.
    assert res.status_code in (200, 422)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/web/test_stock.py -v -k "before"
```

Expected: FAIL — the `before` parameter isn't handled yet.

- [ ] **Step 3: Implement the lazy-load path**

In `marketpulse/web/routes/stock.py`, replace the `stock_chart_data` function (lines 88-163) with the version below. The change adds two new query params (`before`, `count`) and a branch for the yfinance lazy-load path.

```python
@router.get("/stock/{ticker}/chart-data")
def stock_chart_data(
    ticker: str,
    period: str = Query("60d"),
    before: str | None = Query(None),
    count: int = Query(180, ge=1, le=400),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    """Two modes:
    - ?period=...                  initial load (Tencent fast path, unchanged)
    - ?before=YYYY-MM-DD&count=N   lazy-load chunk via yfinance, padded with
                                   250-day indicator lookback then trimmed by
                                   date before returning. `before` wins if both
                                   are provided.
    """
    ticker = ticker.upper()

    if before is not None:
        return _chart_data_lazy(ticker, before, count)

    if period not in _VALID_PERIODS:
        raise HTTPException(
            status_code=422,
            detail=f"period must be one of {sorted(_VALID_PERIODS)}",
        )
    # ---- existing period code path, unchanged ----
    try:
        all_bars = data.get_history(ticker, period="1y")
    except Exception as exc:
        log.warning("chart_data_history_failed", ticker=ticker, error=str(exc))
        all_bars = []

    cutoff = date.today() - timedelta(days=_PERIOD_DAYS[period])

    if not all_bars:
        return JSONResponse(_empty_payload(), headers={"Cache-Control": "private, max-age=300"})

    return JSONResponse(
        _build_payload(all_bars, cutoff=cutoff),
        headers={"Cache-Control": "private, max-age=300"},
    )


_LOOKBACK_DAYS = 250  # buffer for SMA200/EMA26/Bollinger to be valid at window start


def _chart_data_lazy(ticker: str, before_str: str, count: int) -> JSONResponse:
    """Lazy-load path: fetch `count + 250` bars ending strictly before
    `before_str` via yfinance, compute indicators over the padded range,
    then trim by date so only the requested `count` window is returned.
    """
    try:
        before_date = date.fromisoformat(before_str)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"invalid before: {exc}",
        ) from exc

    # We pull (count + lookback) calendar days. Trading days are ~70% of
    # calendar days so this is a comfortable upper bound for the bar count
    # we actually want, and the trim step below ensures we return exactly
    # `count` trading days (or fewer if ticker history is short).
    fetch_start = before_date - timedelta(days=count + _LOOKBACK_DAYS)
    fetch_end = before_date - timedelta(days=1)  # exclusive of `before`

    from marketpulse.data.yfinance_client import YFinanceClient
    try:
        all_bars = YFinanceClient().fetch_history_range(
            ticker, start=fetch_start, end=fetch_end,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("chart_data_lazy_failed", ticker=ticker,
                    before=before_str, error=str(exc))
        all_bars = []

    if not all_bars:
        return JSONResponse(_empty_payload())

    # Bars sort ascending; the "window" is the last `count` of them.
    window_bars = all_bars[-count:]
    window_start = window_bars[0].date

    return JSONResponse(_build_payload(all_bars, cutoff=window_start))


def _empty_payload() -> dict:
    empty: list = []
    return {
        "bars": empty, "ema12": empty, "ema26": empty,
        "sma50": empty, "sma200": empty,
        "bb_upper": empty, "bb_middle": empty, "bb_lower": empty,
        "rsi": empty,
        "macd": {"line": empty, "signal": empty, "histogram": empty},
        "signal_markers": empty,
    }


def _build_payload(all_bars: list, cutoff: date) -> dict:
    """Compute indicators over `all_bars`, then trim every series to points
    whose date >= cutoff (date-based trim so leading-null indicators don't
    misalign when sliced)."""
    closes = [b.close for b in all_bars]
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    bb_upper, bb_middle, bb_lower = bollinger_series(closes)
    rsi = rsi_series(closes)
    macd_line, macd_signal, macd_hist = macd(closes)
    markers = scan_signal_markers(all_bars)

    def series_after(bars, series):
        return [
            {"time": b.date.isoformat(), "value": v}
            for b, v in zip(bars, series, strict=True)
            if b.date >= cutoff
        ]

    visible_bars = [b for b in all_bars if b.date >= cutoff]
    return {
        "bars": [
            {"time": b.date.isoformat(), "open": b.open, "high": b.high,
             "low": b.low, "close": b.close, "volume": b.volume}
            for b in visible_bars
        ],
        "ema12": series_after(all_bars, ema12),
        "ema26": series_after(all_bars, ema26),
        "sma50": series_after(all_bars, sma50),
        "sma200": series_after(all_bars, sma200),
        "bb_upper": series_after(all_bars, bb_upper),
        "bb_middle": series_after(all_bars, bb_middle),
        "bb_lower": series_after(all_bars, bb_lower),
        "rsi": series_after(all_bars, rsi),
        "macd": {
            "line": series_after(all_bars, macd_line),
            "signal": series_after(all_bars, macd_signal),
            "histogram": series_after(all_bars, macd_hist),
        },
        "signal_markers": [m for m in markers if m["time"] >= cutoff.isoformat()],
    }
```

`Query` is already imported from `fastapi` (used elsewhere in this file). The `ge=1, le=400` constraints on `count` give the test `test_chart_data_count_capped_at_max` a clean 422.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/web/test_stock.py -v
```

Expected: All pre-existing stock tests pass + 5 new ones. If the regression test `test_chart_data_period_still_works` fails, the refactor broke the period path; compare `_build_payload`'s output against the original inline code in `stock_chart_data`.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/web/routes/stock.py tests/web/test_stock.py
git commit -m "feat(chart): chart-data accepts before+count for lazy-load (yfinance path)"
```

---

## Task 3: Integration test — full chunk handoff

**Files:**
- Create: `tests/integration/test_chart_data_lazy.py`

- [ ] **Step 1: Write the test**

Create `tests/integration/test_chart_data_lazy.py`:

```python
"""End-to-end-ish test for the chart-data lazy-load handoff.

Confirms that the second chunk's bars strictly precede the first chunk —
the property that lets the frontend prepend without overlap.
"""
from datetime import date, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.data.types import Bar


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _fake_bars(start: date, n: int) -> list[Bar]:
    return [
        Bar(date=start + timedelta(days=i),
            open=10.0, high=10.5, low=9.5,
            close=10.0 + i * 0.01, volume=1_000_000)
        for i in range(n)
    ]


def test_lazy_load_chunk_strictly_precedes_initial(client: TestClient, monkeypatch):
    """Initial ?period=60d returns ~60 bars ending today. Second call with
    ?before=<initial earliest>&count=180 returns 180 bars all strictly before
    that. No overlap, ready to be prepended on the client.
    """
    _login(client, monkeypatch)
    today = date.today()
    # Use a fake yfinance for both calls — the period path doesn't use it,
    # but the lazy path does. We patch only the lazy method.
    fake_lazy = _fake_bars(today - timedelta(days=430), 430)
    with patch(
        "marketpulse.data.yfinance_client.YFinanceClient.fetch_history_range",
        return_value=fake_lazy,
    ):
        # Skip the period leg — it uses real Tencent/yfinance via DataService.
        # Just test the handoff property: lazy call ends before its own `before`.
        before_iso = (today - timedelta(days=60)).isoformat()
        res = client.get(f"/stock/AAPL/chart-data?before={before_iso}&count=180")
        assert res.status_code == 200
        chunk = res.json()
        assert chunk["bars"], "lazy chunk should not be empty when fake data spans the window"
        # Every bar in the chunk must be strictly before `before`.
        assert all(b["time"] < before_iso for b in chunk["bars"])


def test_lazy_load_indicators_align_with_bars(client: TestClient, monkeypatch):
    """Every indicator point's `time` must match one of the bar `time` values
    (no orphan indicator points outside the bar window).
    """
    _login(client, monkeypatch)
    today = date.today()
    fake = _fake_bars(today - timedelta(days=430), 430)
    with patch(
        "marketpulse.data.yfinance_client.YFinanceClient.fetch_history_range",
        return_value=fake,
    ):
        res = client.get(
            f"/stock/AAPL/chart-data?before={today.isoformat()}&count=180",
        )
    assert res.status_code == 200
    body = res.json()
    bar_times = {b["time"] for b in body["bars"]}
    for series_name in ("ema12", "sma50", "sma200", "bb_upper", "rsi"):
        for p in body[series_name]:
            assert p["time"] in bar_times, (
                f"{series_name} point at {p['time']} has no matching bar"
            )
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
python -m pytest tests/integration/test_chart_data_lazy.py -v
```

Expected: PASS, 2 tests.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_chart_data_lazy.py
git commit -m "test(chart): lazy-load chunk handoff invariants"
```

---

## Task 4: Frontend — state, prepend, scroll trigger

**Files:**
- Modify: `marketpulse/web/static/chart.js`

- [ ] **Step 1: Refactor `renderCharts` to store series handles in state**

In `marketpulse/web/static/chart.js`, replace the existing `seriesRefs` block (line 16) and `renderCharts` function with the updated version below.

Two structural changes:
1. `seriesRefs` becomes part of a richer `window.__mpChartState` that also holds the underlying data arrays so we can prepend later.
2. The function now records every series handle and the initial data arrays into state, plus the `oldestLoaded` cursor.

Replace lines 15-160 (the entire `seriesRefs` declaration through `mainChart.timeScale().fitContent();`) with:

```javascript
  // Module-level state shared across initial render and lazy-load chunks.
  // Stored on `window` so subsequent fetches in the same session can prepend.
  function freshState() {
    return {
      ticker: null,
      bars: [],
      ema12: [], ema26: [],
      sma50: [], sma200: [],
      bb_upper: [], bb_middle: [], bb_lower: [],
      rsi: [],
      macd: { line: [], signal: [], histogram: [] },
      signal_markers: [],
      oldestLoaded: null,
      hasMoreHistory: true,
      loadingMore: false,
      // Series handles populated during renderCharts so prependChunk can setData.
      mainChart: null, candleSeries: null, volSeries: null,
      ema12Series: null, ema26Series: null,
      sma50Series: null, sma200Series: null,
      bbUpperSeries: null, bbLowerSeries: null,
      rsiChart: null, rsiSeries: null,
      macdChart: null, macdLineSeries: null, macdSignalSeries: null, macdHistSeries: null,
    };
  }

  function densify(series) {
    return series.filter(p => p.value !== null && p.value !== undefined);
  }

  function renderCharts(payload, ticker) {
    const mainEl = document.getElementById("chart-main");
    mainEl.innerHTML = "";
    document.getElementById("chart-rsi").innerHTML = "";
    document.getElementById("chart-macd").innerHTML = "";

    // Reset state for the new render (new ticker, new period, or first load).
    const s = freshState();
    s.ticker = ticker;
    window.__mpChartState = s;

    if (!payload.bars || payload.bars.length === 0) {
      mainEl.innerHTML =
        '<p class="text-slate-500 text-sm py-8 text-center">暂无 K 线数据</p>';
      return;
    }

    // Snapshot the initial data into state so future prepends can extend it.
    s.bars = payload.bars.slice();
    s.ema12 = (payload.ema12 || []).slice();
    s.ema26 = (payload.ema26 || []).slice();
    s.sma50 = (payload.sma50 || []).slice();
    s.sma200 = (payload.sma200 || []).slice();
    s.bb_upper = (payload.bb_upper || []).slice();
    s.bb_middle = (payload.bb_middle || []).slice();
    s.bb_lower = (payload.bb_lower || []).slice();
    s.rsi = (payload.rsi || []).slice();
    s.macd.line = (payload.macd?.line || []).slice();
    s.macd.signal = (payload.macd?.signal || []).slice();
    s.macd.histogram = (payload.macd?.histogram || []).slice();
    s.signal_markers = (payload.signal_markers || []).slice();
    s.oldestLoaded = s.bars[0].time;

    const commonOpts = {
      autoSize: true,
      layout: { background: { color: "#ffffff" }, textColor: "#334155" },
      grid: { vertLines: { color: "#e2e8f0" }, horzLines: { color: "#e2e8f0" } },
      timeScale: { borderColor: "#cbd5e1", rightOffset: 12 },
      crosshair: { mode: 0 },
    };

    const lineOpts = (extras) => Object.assign({
      lineWidth: 1, priceLineVisible: false,
    }, extras);

    // === Main chart ===
    s.mainChart = LightweightCharts.createChart(mainEl, commonOpts);
    s.candleSeries = s.mainChart.addCandlestickSeries({
      upColor: "#16a34a", downColor: "#dc2626",
      borderVisible: false, wickUpColor: "#16a34a", wickDownColor: "#dc2626",
    });
    s.candleSeries.setData(s.bars);

    function addLineIfData(data, opts, handleKey) {
      const dense = densify(data);
      if (dense.length === 0) return null;
      const line = s.mainChart.addLineSeries(opts);
      line.setData(dense);
      if (handleKey) s[handleKey] = line;
      return line;
    }
    s.ema12Series   = addLineIfData(s.ema12,   lineOpts({ color: "#0ea5e9", title: "EMA12" }),    "ema12Series");
    s.ema26Series   = addLineIfData(s.ema26,   lineOpts({ color: "#f59e0b", title: "EMA26" }),    "ema26Series");
    s.sma50Series   = addLineIfData(s.sma50,   lineOpts({ color: "#8b5cf6", title: "SMA50" }),    "sma50Series");
    s.sma200Series  = addLineIfData(s.sma200,  lineOpts({ color: "#64748b", title: "SMA200" }),   "sma200Series");
    s.bbUpperSeries = addLineIfData(s.bb_upper, lineOpts({ color: "#a855f7", lineStyle: 2, title: "BB上轨" }), "bbUpperSeries");
    s.bbLowerSeries = addLineIfData(s.bb_lower, lineOpts({ color: "#a855f7", lineStyle: 2, title: "BB下轨" }), "bbLowerSeries");
    applyToggles();

    s.volSeries = s.mainChart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
      scaleMargins: { top: 0.85, bottom: 0 },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    s.volSeries.setData(s.bars.map(b => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? "rgba(22,163,74,0.4)" : "rgba(220,38,38,0.4)",
    })));

    if (s.signal_markers.length > 0) {
      const markers = s.signal_markers.map(m => {
        const style = SIGNAL_STYLES[m.type] || { shape: "circle", color: "#475569", text: m.type };
        return {
          time: m.time, position: "aboveBar",
          color: style.color, shape: style.shape, text: style.text,
        };
      });
      s.candleSeries.setMarkers(markers);
    }

    // === RSI pane ===
    const rsiData = densify(s.rsi);
    if (rsiData.length > 0) {
      s.rsiChart = LightweightCharts.createChart(
        document.getElementById("chart-rsi"),
        Object.assign({}, commonOpts, {
          rightPriceScale: { scaleMargins: { top: 0.15, bottom: 0.15 } },
        }),
      );
      s.rsiSeries = s.rsiChart.addLineSeries(lineOpts({ color: "#9333ea" }));
      s.rsiSeries.setData(rsiData);
      const ob = s.rsiChart.addLineSeries(lineOpts({
        color: "#fca5a5", lineStyle: 2, lastValueVisible: false,
      }));
      ob.setData(rsiData.map(p => ({ time: p.time, value: 70 })));
      const os = s.rsiChart.addLineSeries(lineOpts({
        color: "#93c5fd", lineStyle: 2, lastValueVisible: false,
      }));
      os.setData(rsiData.map(p => ({ time: p.time, value: 30 })));
    }

    // === MACD pane ===
    const macdLine = densify(s.macd.line);
    if (macdLine.length > 0) {
      s.macdChart = LightweightCharts.createChart(
        document.getElementById("chart-macd"),
        Object.assign({}, commonOpts, {
          rightPriceScale: { scaleMargins: { top: 0.15, bottom: 0.15 } },
        }),
      );
      s.macdLineSeries = s.macdChart.addLineSeries(lineOpts({ color: "#0ea5e9" }));
      s.macdLineSeries.setData(macdLine);
      s.macdSignalSeries = s.macdChart.addLineSeries(lineOpts({ color: "#f59e0b" }));
      s.macdSignalSeries.setData(densify(s.macd.signal));
      s.macdHistSeries = s.macdChart.addHistogramSeries({
        priceLineVisible: false, lastValueVisible: false,
      });
      s.macdHistSeries.setData(densify(s.macd.histogram).map(p => ({
        time: p.time, value: p.value,
        color: p.value >= 0 ? "rgba(22,163,74,0.6)" : "rgba(220,38,38,0.6)",
      })));
    }

    // Sync time scale across panes (main ↔ RSI ↔ MACD).
    const syncPair = (a, b) => {
      a.timeScale().subscribeVisibleTimeRangeChange(r => {
        if (!r) return;
        b.timeScale().setVisibleRange(r);
      });
    };
    if (s.rsiChart)  { syncPair(s.mainChart, s.rsiChart);  syncPair(s.rsiChart, s.mainChart); }
    if (s.macdChart) { syncPair(s.mainChart, s.macdChart); syncPair(s.macdChart, s.mainChart); }

    s.mainChart.timeScale().fitContent();

    // Lazy-load trigger: re-fetch older history when scroll nears left edge.
    s.mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (!range) return;
      // range.from is the LEFTMOST visible logical index relative to bars[0].
      // Trigger fetch when leftmost visible bar is within 30 of bars[0].
      if (range.from < 30) {
        loadMoreHistory();
      }
    });
  }
```

The existing `applyToggles` function still references `seriesRefs.bb_upper` etc. — update it now to read from the new state. Replace the existing `applyToggles` (lines 162-169 of the original file) with:

```javascript
  function applyToggles() {
    const s = window.__mpChartState;
    if (!s) return;
    const bbOn  = document.getElementById("toggle-bb")?.checked  ?? true;
    const smaOn = document.getElementById("toggle-sma")?.checked ?? true;
    if (s.bbUpperSeries) s.bbUpperSeries.applyOptions({ visible: bbOn });
    if (s.bbLowerSeries) s.bbLowerSeries.applyOptions({ visible: bbOn });
    if (s.sma50Series)   s.sma50Series.applyOptions({ visible: smaOn });
    if (s.sma200Series)  s.sma200Series.applyOptions({ visible: smaOn });
  }
```

- [ ] **Step 2: Add `loadMoreHistory` and `prependChunk` functions**

Inside the same IIFE in `marketpulse/web/static/chart.js`, AFTER `applyToggles` and BEFORE `async function load(ticker, period)`, add:

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
      if (s.ticker !== tickerAtRequest) return;  // user switched mid-fetch
      if (!chunk.bars || chunk.bars.length === 0) {
        s.hasMoreHistory = false;
        return;
      }
      prependChunk(chunk);
      s.oldestLoaded = chunk.bars[0].time;
    } catch (exc) {
      console.warn("lazy-load failed:", exc);
    } finally {
      s.loadingMore = false;
      showLoadingDot(false);
    }
  }

  function prependChunk(chunk) {
    const s = window.__mpChartState;
    // Bars: prepend, then setData on candle + volume series.
    s.bars = chunk.bars.concat(s.bars);
    s.candleSeries.setData(s.bars);
    s.volSeries.setData(s.bars.map(b => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? "rgba(22,163,74,0.4)" : "rgba(220,38,38,0.4)",
    })));

    // Each line/indicator: extend the in-state array, then setData on its handle.
    const extendAndSet = (key, handle) => {
      if (!handle) return;
      const incoming = chunk[key] || [];
      s[key] = incoming.concat(s[key]);
      handle.setData(densify(s[key]));
    };
    extendAndSet("ema12",   s.ema12Series);
    extendAndSet("ema26",   s.ema26Series);
    extendAndSet("sma50",   s.sma50Series);
    extendAndSet("sma200",  s.sma200Series);
    extendAndSet("bb_upper", s.bbUpperSeries);
    extendAndSet("bb_lower", s.bbLowerSeries);
    extendAndSet("rsi",     s.rsiSeries);

    // MACD has a nested shape.
    if (s.macdLineSeries) {
      s.macd.line = (chunk.macd?.line || []).concat(s.macd.line);
      s.macdLineSeries.setData(densify(s.macd.line));
    }
    if (s.macdSignalSeries) {
      s.macd.signal = (chunk.macd?.signal || []).concat(s.macd.signal);
      s.macdSignalSeries.setData(densify(s.macd.signal));
    }
    if (s.macdHistSeries) {
      s.macd.histogram = (chunk.macd?.histogram || []).concat(s.macd.histogram);
      s.macdHistSeries.setData(densify(s.macd.histogram).map(p => ({
        time: p.time, value: p.value,
        color: p.value >= 0 ? "rgba(22,163,74,0.6)" : "rgba(220,38,38,0.6)",
      })));
    }

    // Markers (rare — usually only on right side, but include for completeness)
    if (chunk.signal_markers && chunk.signal_markers.length > 0) {
      s.signal_markers = chunk.signal_markers.concat(s.signal_markers);
      const markers = s.signal_markers.map(m => {
        const style = SIGNAL_STYLES[m.type] || { shape: "circle", color: "#475569", text: m.type };
        return {
          time: m.time, position: "aboveBar",
          color: style.color, shape: style.shape, text: style.text,
        };
      });
      s.candleSeries.setMarkers(markers);
    }
  }

  function showLoadingDot(on) {
    const dot = document.getElementById("chart-loading-dot");
    if (!dot) return;
    dot.classList.toggle("opacity-0", !on);
    dot.classList.toggle("opacity-70", on);
  }
```

- [ ] **Step 3: Update `load(ticker, period)` to pass ticker to renderCharts**

The existing `load` function calls `renderCharts(await r.json())` with only one arg. Update it (around line 178 of the original file) to pass the ticker as the second arg:

```javascript
  async function load(ticker, period) {
    const r = await fetch(`/stock/${ticker}/chart-data?period=${period}`);
    if (!r.ok) {
      document.getElementById("chart-main").innerHTML =
        `<p class="text-red-600 text-sm py-8 text-center">加载失败: ${r.status}</p>`;
      return;
    }
    renderCharts(await r.json(), ticker);
  }
```

- [ ] **Step 4: Manual smoke test**

Run the dev server:

```bash
source .venv/bin/activate
DATABASE_URL="sqlite:///./data/marketpulse.db" \
    APP_PASSWORD_HASH="$(python -c 'from marketpulse.auth.password import hash_password; print(hash_password("test"))')" \
    SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
    ANTHROPIC_API_KEY="dummy" \
    uvicorn marketpulse.web.main:app --reload --port 8089
```

Open `http://localhost:8089/stock/AAPL`, scroll left with mouse wheel. Expected: as the leftmost visible bar approaches the leftmost loaded bar, new bars appear on the left without the visible time range jumping.

If you don't have local Tencent/yfinance access, the dev server may fail on initial chart data — that's fine, the manual smoke test is optional. The unit + integration tests are the primary verification.

- [ ] **Step 5: Run all backend tests as regression**

```bash
python -m pytest tests/web/test_stock.py tests/integration/test_chart_data_lazy.py tests/unit/test_yfinance_history_range.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/web/static/chart.js
git commit -m "feat(chart): frontend lazy-load on scroll near left edge"
```

---

## Task 5: Loading-dot CSS and template hook

**Files:**
- Modify: `marketpulse/web/templates/stock.html`

- [ ] **Step 1: Add the loading-dot element to the template**

Find the `<div id="chart-main">` element in `marketpulse/web/templates/stock.html`. It currently looks something like `<div id="chart-main" data-ticker="{{ ticker }}" class="..."></div>`.

a) Wrap the chart-main in a `relative`-positioned container, OR add `relative` to the existing container if there is one.

b) Add the loading-dot div as a sibling INSIDE that relative container, immediately after the chart-main element. Example final markup:

```html
<div class="relative">
  <div id="chart-main" data-ticker="{{ ticker }}" class="w-full h-96"></div>
  <div id="chart-loading-dot"
       class="absolute top-2 left-2 w-2 h-2 rounded-full bg-slate-400
              opacity-0 transition-opacity duration-200 pointer-events-none
              animate-pulse"></div>
</div>
```

Read the existing template first to find the actual class names and wrapping; modify minimally to keep the existing layout. The only essential properties:
- The parent of `chart-loading-dot` must have `position: relative` (Tailwind's `relative`)
- The dot itself uses `absolute`, `top-2 left-2`, `opacity-0`, plus `transition-opacity` so the JS toggle animates smoothly

- [ ] **Step 2: Verify the dot renders (manual)**

If the dev server is running from Task 4 Step 4, refresh `/stock/AAPL`. The dot should be invisible by default (`opacity-0`). Open dev tools and temporarily run:

```javascript
document.getElementById("chart-loading-dot").classList.remove("opacity-0")
document.getElementById("chart-loading-dot").classList.add("opacity-70")
```

Expected: small gray pulsing dot appears at top-left of the chart. Then:

```javascript
document.getElementById("chart-loading-dot").classList.add("opacity-0")
document.getElementById("chart-loading-dot").classList.remove("opacity-70")
```

Expected: dot fades out.

This is a visual verification only; not gated by automated tests.

- [ ] **Step 3: Commit**

```bash
git add marketpulse/web/templates/stock.html
git commit -m "feat(chart): add loading-dot overlay for lazy-load feedback"
```

---

## Task 6: Full-suite regression + ruff

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

```bash
source .venv/bin/activate
python -m pytest -q
```

Expected: all tests pass. Count should be the prior 283 + 9 new from Tasks 1-3 = approximately 292.

- [ ] **Step 2: Ruff check**

```bash
ruff check marketpulse tests
```

Expected: `All checks passed!`

If ruff finds anything, run `ruff check marketpulse tests --fix` and re-run pytest to confirm no behavior change.

- [ ] **Step 3: No commit needed** — Task 6 is verification only.

---

## After-deployment notes

- The lazy-load path uses `YFinanceClient.fetch_history_range`, which goes through Mihomo per the existing yfinance routing in the deployment. If Mihomo is down, lazy-load chunks will fail silently (per spec) — `console.warn` in the browser, no user-visible error. The initial period load still uses Tencent and works regardless.
- Browser cache header is intentionally NOT added on the lazy-load response in v1 to keep the path simple. Future Optimizations in the spec calls this out.
