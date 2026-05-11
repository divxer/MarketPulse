# K-Line Chart + Daily Recap Push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render TradingView lightweight-charts on the stock detail page, and push a daily recap summary to the configured notifier.

**Architecture:** Two independent phases. **Phase A** adds backend indicator computations (SMA, MACD, signal-marker scan), a new JSON endpoint that returns all chart data + indicators, and a frontend chart module. **Phase B** adds a recap-push module and wires it into the daily recap scheduler hook.

**Tech Stack:** Python 3.12 / FastAPI / Jinja / SQLAlchemy 2 / APScheduler (existing), tenacity (existing dep used for retry), TradingView lightweight-charts v4 (new, via jsDelivr CDN, no npm install needed)

**Reference spec:** [docs/superpowers/specs/2026-05-11-kline-chart-and-recap-push-design.md](../specs/2026-05-11-kline-chart-and-recap-push-design.md)

**Phase order:** A and B are fully independent. Recommended order is A first (more code, visual reward), then B. Either can ship without the other.

---

## Phase A — K-Line Chart

### Task A1: SMA helper in signals module

**Files:**
- Modify: `marketpulse/recap/signals.py` (add `sma()` near `_ema()`)
- Test: `tests/unit/test_signals.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_signals.py`:

```python
from marketpulse.recap.signals import sma


def test_sma_basic():
    # SMA with period=3 over [1,2,3,4,5] → [None, None, 2.0, 3.0, 4.0]
    out = sma([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    assert out == [None, None, 2.0, 3.0, 4.0]


def test_sma_period_longer_than_input():
    assert sma([1.0, 2.0], 5) == [None, None]


def test_sma_period_one_returns_input():
    assert sma([1.0, 2.0, 3.0], 1) == [1.0, 2.0, 3.0]


def test_sma_empty_input():
    assert sma([], 5) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_signals.py::test_sma_basic -v`
Expected: FAIL with `ImportError: cannot import name 'sma'`

- [ ] **Step 3: Add `sma()` to `marketpulse/recap/signals.py`**

Add at module level (after the `_ema` helper, before `_ema_cross`):

```python
def sma(values: list[float], period: int) -> list[float | None]:
    """Simple moving average. Returns one entry per input position.
    Positions where the window isn't yet filled (< period values seen)
    return None, matching the lightweight-charts sparse-series convention.
    """
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(sum(values[i - period + 1 : i + 1]) / period)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_signals.py -v`
Expected: all SMA tests PASS, existing tests still pass

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_signals.py marketpulse/recap/signals.py
git commit -m "feat(signals): add sma() public helper for chart overlays"
```

---

### Task A2: MACD helper in signals module

**Files:**
- Modify: `marketpulse/recap/signals.py` (add `macd()` + public `ema()`)
- Test: `tests/unit/test_signals.py`

The existing `_ema` is private with non-sparse output (length = `len(values) - period + 1`). MACD needs a *sparse* EMA (same length as input, leading Nones) for proper alignment with the rest of the chart series. We add a new public `ema()` with that convention and reuse it from `macd()`. The private `_ema` keeps its existing callers untouched.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_signals.py`:

```python
from marketpulse.recap.signals import ema, macd


def test_ema_sparse_returns_input_length_with_leading_nones():
    # Period 3: first 2 entries are None, then EMAs
    out = ema([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    assert len(out) == 5
    assert out[0] is None
    assert out[1] is None
    # Seed = simple mean of first 3 = 2.0
    assert out[2] == 2.0
    # multiplier = 2/(3+1) = 0.5; out[3] = (4 - 2) * 0.5 + 2 = 3.0
    assert out[3] == 3.0
    # out[4] = (5 - 3) * 0.5 + 3 = 4.0
    assert out[4] == 4.0


def test_ema_too_short_all_nones():
    assert ema([1.0, 2.0], 5) == [None, None]


def test_macd_basic_shape():
    # 50 ascending values → all three series should compute past warm-up
    values = [float(i) for i in range(1, 51)]
    line, signal, hist = macd(values, fast=12, slow=26, signal=9)
    assert len(line) == 50
    assert len(signal) == 50
    assert len(hist) == 50
    # First slow-1=25 entries of line are None (need full slow EMA)
    assert line[24] is None
    assert line[25] is not None
    # Signal line warm-up: slow-1 + (signal-1) = 25 + 8 = 33
    assert signal[32] is None
    assert signal[33] is not None
    # Histogram = line - signal where both are non-None
    assert hist[40] == line[40] - signal[40]


def test_macd_too_short_all_nones():
    values = [1.0, 2.0, 3.0]
    line, signal, hist = macd(values)
    assert all(v is None for v in line)
    assert all(v is None for v in signal)
    assert all(v is None for v in hist)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_signals.py::test_macd_basic_shape -v`
Expected: FAIL with `ImportError: cannot import name 'macd'`

- [ ] **Step 3: Add `ema()` and `macd()` to `marketpulse/recap/signals.py`**

Add at module level (immediately after the existing `_ema` function):

```python
def ema(values: list[float], period: int) -> list[float | None]:
    """Sparse EMA — same length as input, leading Nones where window isn't filled.

    Public counterpart to `_ema` which returns a shorter array. Used by `macd()`
    and the chart-data endpoint where every series must align by index.
    """
    out: list[float | None] = []
    if len(values) < period:
        return [None] * len(values)
    multiplier = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out.extend([None] * (period - 1))
    out.append(seed)
    for v in values[period:]:
        prev = out[-1]
        assert prev is not None  # invariant: out[period-1:] is dense
        out.append((v - prev) * multiplier + prev)
    return out


def macd(
    values: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """MACD line, signal line, and histogram. Each is the same length as `values`,
    with leading Nones during indicator warm-up.

    line = EMA(fast) - EMA(slow)
    signal = EMA(line, signal)
    histogram = line - signal
    """
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    # MACD line is defined where slow EMA is.
    line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_ema, slow_ema, strict=True)
    ]
    # Signal line is EMA of the dense tail of line.
    dense_tail = [v for v in line if v is not None]
    if len(dense_tail) >= signal:
        signal_tail = ema(dense_tail, signal)
        # Re-pad with Nones for positions where line itself was None.
        pad = len(line) - len(signal_tail)
        signal_line: list[float | None] = [None] * pad + signal_tail
    else:
        signal_line = [None] * len(line)
    hist = [
        (l - s) if (l is not None and s is not None) else None
        for l, s in zip(line, signal_line, strict=True)
    ]
    return line, signal_line, hist
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_signals.py -v`
Expected: all SMA + MACD + EMA tests PASS, existing tests still pass

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_signals.py marketpulse/recap/signals.py
git commit -m "feat(signals): add public ema() and macd() for chart overlays"
```

---

### Task A3: Signal marker scan across a price series

**Files:**
- Modify: `marketpulse/recap/signals.py` (add `scan_signal_markers()`)
- Test: `tests/unit/test_signals.py`

The existing `detect_signals()` only looks at the latest bar. For the chart we need every historical bar where each signal first fires. The new function walks the bar series and emits one marker per (signal, first-fire) pair.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_signals.py`:

```python
from datetime import date as _d, timedelta

from marketpulse.data.types import Bar
from marketpulse.recap.signals import scan_signal_markers


def _bar(d: _d, close: float, vol: int = 1_000_000) -> Bar:
    return Bar(date=d, open=close, high=close, low=close, close=close, volume=vol)


def test_scan_signal_markers_detects_ema_golden_cross():
    # Construct a series where EMA12 crosses above EMA26 once.
    # Long downtrend then sharp recovery → guaranteed cross.
    today = _d.today()
    closes = list(range(100, 50, -1)) + list(range(50, 100))  # 100 bars
    bars = [_bar(today - timedelta(days=len(closes) - i), c) for i, c in enumerate(closes)]
    markers = scan_signal_markers(bars)
    types = [m["type"] for m in markers]
    assert "ema_golden_cross" in types


def test_scan_signal_markers_emits_once_not_per_bar():
    # If RSI stays >= 70 for many bars, we want one marker at the first cross,
    # not one marker per bar.
    today = _d.today()
    closes = [10.0] * 30 + list(range(10, 60))  # flat then sharp rise → overbought sustained
    bars = [_bar(today - timedelta(days=len(closes) - i), float(c)) for i, c in enumerate(closes)]
    markers = scan_signal_markers(bars)
    overbought = [m for m in markers if m["type"] == "rsi_overbought"]
    assert len(overbought) <= 1


def test_scan_signal_markers_empty_series():
    assert scan_signal_markers([]) == []


def test_scan_signal_markers_each_marker_has_required_fields():
    today = _d.today()
    closes = list(range(100, 50, -1)) + list(range(50, 100))
    bars = [_bar(today - timedelta(days=len(closes) - i), c) for i, c in enumerate(closes)]
    markers = scan_signal_markers(bars)
    for m in markers:
        assert set(m.keys()) >= {"time", "type", "note"}
        assert isinstance(m["time"], str)  # ISO date for JSON
        assert isinstance(m["type"], str)
        assert isinstance(m["note"], str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_signals.py::test_scan_signal_markers_empty_series -v`
Expected: FAIL with `ImportError: cannot import name 'scan_signal_markers'`

- [ ] **Step 3: Add `scan_signal_markers()` to `marketpulse/recap/signals.py`**

Add at module level (after `detect_signals`):

```python
def scan_signal_markers(bars: list[Bar]) -> list[dict[str, str]]:
    """Walk the series and emit one marker per (signal_type, first-fire) pair.

    A signal is considered "firing" at bar i if it would have been emitted by
    detect_signals() on the prefix bars[:i+1]. Once a signal type fires, it is
    deduplicated until it stops firing for at least one bar (so a sustained
    overbought RSI gets one marker on entry, not one per bar).
    """
    if not bars:
        return []

    markers: list[dict[str, str]] = []
    # For each signal type, was it firing on the previous bar?
    previously_firing: dict[str, bool] = {}

    closes = [b.close for b in bars]

    def _add(i: int, signal_type: str, note: str) -> None:
        was_firing = previously_firing.get(signal_type, False)
        if not was_firing:
            markers.append({
                "time": bars[i].date.isoformat(),
                "type": signal_type,
                "note": note,
            })
        previously_firing[signal_type] = True

    def _clear(signal_type: str) -> None:
        previously_firing[signal_type] = False

    # Indicators precomputed over the full series for efficient lookup.
    ema12 = ema(closes, EMA_SHORT)
    ema26 = ema(closes, EMA_LONG)

    for i in range(len(bars)):
        prefix_closes = closes[: i + 1]
        prefix_bars = bars[: i + 1]

        # EMA cross — use precomputed series and look at index i vs i-1
        if i >= 1 and ema12[i] is not None and ema26[i] is not None \
                and ema12[i - 1] is not None and ema26[i - 1] is not None:
            prev_diff = ema12[i - 1] - ema26[i - 1]
            curr_diff = ema12[i] - ema26[i]
            if prev_diff <= 0 < curr_diff:
                _add(i, "ema_golden_cross",
                     f"EMA12 (${ema12[i]:.2f}) crossed above EMA26 (${ema26[i]:.2f})")
                _clear("ema_death_cross")
            elif prev_diff >= 0 > curr_diff:
                _add(i, "ema_death_cross",
                     f"EMA12 (${ema12[i]:.2f}) crossed below EMA26 (${ema26[i]:.2f})")
                _clear("ema_golden_cross")

        # RSI overbought / oversold (need full _rsi to handle Wilder's smoothing)
        rsi_val = _rsi(prefix_closes)
        if rsi_val is not None:
            if rsi_val >= RSI_OVERBOUGHT:
                _add(i, "rsi_overbought", f"RSI(14) = {rsi_val:.1f} (≥ {RSI_OVERBOUGHT:.0f})")
                _clear("rsi_oversold")
            elif rsi_val <= RSI_OVERSOLD:
                _add(i, "rsi_oversold", f"RSI(14) = {rsi_val:.1f} (≤ {RSI_OVERSOLD:.0f})")
                _clear("rsi_overbought")
            else:
                _clear("rsi_overbought")
                _clear("rsi_oversold")

        # Bollinger band touch
        band = _bollinger(prefix_closes)
        if band:
            upper, _, lower = band
            last_close = prefix_closes[-1]
            if upper > lower + 1e-9:
                if last_close > upper:
                    _add(i, "bollinger_upper",
                         f"Close ${last_close:.2f} above upper band ${upper:.2f}")
                    _clear("bollinger_lower")
                elif last_close < lower:
                    _add(i, "bollinger_lower",
                         f"Close ${last_close:.2f} below lower band ${lower:.2f}")
                    _clear("bollinger_upper")
                else:
                    _clear("bollinger_upper")
                    _clear("bollinger_lower")

    return markers
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_signals.py -v`
Expected: all scan_signal_markers tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_signals.py marketpulse/recap/signals.py
git commit -m "feat(signals): scan_signal_markers() emits one marker per signal first-fire"
```

---

### Task A4: DataService.get_history accepts multi-period strings

**Files:**
- Modify: `marketpulse/data/service.py` (lines around `get_history`)
- Test: `tests/unit/test_data_service.py`

Currently `get_history` parses `"Xd"` only. We need to accept `30d|60d|6m|1y`. For SMA200 alignment, the implementation must also fetch ~200 trading days of headroom and trim by date *after* indicator computation — but indicator computation lives in the route (next task), so this task just expands the parsing.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_data_service.py` (create the file if it doesn't exist):

```python
from datetime import UTC, date, datetime

from marketpulse.data.service import DataService
from marketpulse.data.types import Bar


class _FakeYF:
    def __init__(self):
        self.last_period: str | None = None
        self.bars_to_return: list[Bar] = []

    def fetch_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        self.last_period = period
        return self.bars_to_return

    def fetch_quote(self, ticker): raise NotImplementedError
    def fetch_news(self, ticker, limit=10): return []
    def fetch_fundamentals(self, ticker): raise NotImplementedError
    def fetch_market_overview(self): raise NotImplementedError


def test_get_history_accepts_30d_60d_6m_1y(db_session):
    yf = _FakeYF()
    svc = DataService(db_session, yf)
    for period in ("30d", "60d", "6m", "1y"):
        svc.get_history("AAPL", period=period)
        assert yf.last_period == period


def test_get_history_rejects_unknown_period(db_session):
    yf = _FakeYF()
    svc = DataService(db_session, yf)
    import pytest
    with pytest.raises(ValueError, match="unsupported period"):
        svc.get_history("AAPL", period="invalid")
```

If `tests/unit/test_data_service.py` doesn't exist yet, also add the imports at the top of the file:

```python
import pytest
```

The `db_session` fixture is already defined in `tests/conftest.py` for other tests — verify with:
`grep -n "def db_session" tests/conftest.py`

If it doesn't exist there, add a minimal one in `tests/conftest.py`:

```python
@pytest.fixture
def db_session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    from marketpulse.db.base import get_engine, session_scope
    from marketpulse.db.models import Base
    Base.metadata.create_all(get_engine())
    gen = session_scope()
    s = next(gen)
    try:
        yield s
    finally:
        s.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_data_service.py -v`
Expected: FAIL — current `get_history` will pass `"6m"` and `"1y"` through unchanged but won't translate them; the second test (`test_get_history_rejects_unknown_period`) fails because no validation exists yet.

- [ ] **Step 3: Update `get_history` in `marketpulse/data/service.py`**

Replace the existing `get_history` method (currently the first lines compute `days = int(period.rstrip("d")) if period.endswith("d") else 60`) with:

```python
_PERIOD_DAYS = {
    "30d": 30,
    "60d": 60,
    "6m": 180,
    "1y": 365,
}

# ... inside the class ...

def get_history(self, ticker: str, period: str = "60d") -> list[Bar]:
    if period not in _PERIOD_DAYS:
        raise ValueError(f"unsupported period {period!r} (use one of {sorted(_PERIOD_DAYS)})")
    days = _PERIOD_DAYS[period]
    end = date.today()
    start = end - timedelta(days=days)
    cached = self.price_cache.get_range(ticker, start, end)
    if cached and (end - cached[-1].date).days <= 1:
        return cached
    try:
        bars = self.yf.fetch_history(ticker, period=period)
        self.price_cache.upsert(ticker, bars)
        return bars
    except Exception as exc:
        log.warning("history_fetch_failed", ticker=ticker, error=str(exc))
        return cached
```

Note `_PERIOD_DAYS` belongs at module scope, above the class definition.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_data_service.py -v`
Expected: PASS

Also run the full suite — touching service.py can affect other tests:
Run: `.venv/bin/pytest tests/ -q`
Expected: 143+ passed (no regression)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_data_service.py marketpulse/data/service.py
git commit -m "feat(data): get_history accepts 30d/60d/6m/1y period strings"
```

---

### Task A5: Expand TencentClient + YFinanceClient to honor multi-period

**Files:**
- Modify: `marketpulse/data/tencent_client.py` (line that parses `period` in `fetch_history`)
- Modify: `marketpulse/data/yfinance_client.py` (mapping from our period to yfinance's period strings)
- Test: `tests/unit/test_tencent_client.py`

Tencent's `fetch_history` currently does `int(period.rstrip("d")) if period.endswith("d") else 60` — same bug. yfinance accepts its own period strings (`"1mo"`, `"3mo"`, `"6mo"`, `"1y"`, `"max"`); we need to map ours.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_tencent_client.py`:

```python
@respx.mock
def test_fetch_history_accepts_1y_period() -> None:
    today = _date.today()
    rows = [
        [(today - __import__("datetime").timedelta(days=300)).isoformat(),
         "100.00", "101.00", "102.00", "99.00", "1000"],
        [today.isoformat(), "110.00", "111.00", "112.00", "109.00", "2000"],
    ]
    respx.get("https://web.ifzq.gtimg.cn/appstock/app/usFqKline/get").mock(
        return_value=httpx.Response(200, text=_kline_envelope("usAAPL", rows)),
    )
    bars = TencentClient().fetch_history("AAPL", period="1y")
    # 300 days ago is inside a 1y (365 day) window → both rows kept
    assert len(bars) == 2


@respx.mock
def test_fetch_history_accepts_6m_period() -> None:
    today = _date.today()
    rows = [
        [(today - __import__("datetime").timedelta(days=200)).isoformat(),
         "100.00", "101.00", "102.00", "99.00", "1000"],
        [today.isoformat(), "110.00", "111.00", "112.00", "109.00", "2000"],
    ]
    respx.get("https://web.ifzq.gtimg.cn/appstock/app/usFqKline/get").mock(
        return_value=httpx.Response(200, text=_kline_envelope("usAAPL", rows)),
    )
    bars = TencentClient().fetch_history("AAPL", period="6m")
    # 200 days ago is OUTSIDE 6m (180 day) window → only today kept
    assert len(bars) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_tencent_client.py::test_fetch_history_accepts_1y_period -v`
Expected: FAIL (parsing returns 60 days for non-d periods, cutoff too aggressive)

- [ ] **Step 3: Update `TencentClient.fetch_history` period parsing**

In `marketpulse/data/tencent_client.py`, replace:

```python
days = int(period.rstrip("d")) if period.endswith("d") else 60
```

With:

```python
_PERIOD_DAYS = {"30d": 30, "60d": 60, "6m": 180, "1y": 365}
days = _PERIOD_DAYS.get(period, 60)
```

`_PERIOD_DAYS` should be defined at module scope near the top of the file (after imports).

- [ ] **Step 4: Update `YFinanceClient.fetch_history` mapping**

In `marketpulse/data/yfinance_client.py`, find where it forwards `period` to yfinance and translate ours to theirs:

```python
_YF_PERIOD_MAP = {
    "30d": "1mo",
    "60d": "3mo",
    "6m": "6mo",
    "1y": "1y",
}
# In fetch_history:
yf_period = _YF_PERIOD_MAP.get(period, "3mo")
# pass yf_period to yfinance.Ticker(...).history(period=yf_period)
```

Place `_YF_PERIOD_MAP` at module scope.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_tencent_client.py tests/unit/test_hybrid_client.py -v`
Expected: all pass

Run full suite:
Run: `.venv/bin/pytest tests/ -q`
Expected: 145+ passed (added 2 tencent tests in addition to data_service tests)

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_tencent_client.py marketpulse/data/tencent_client.py marketpulse/data/yfinance_client.py
git commit -m "feat(data): TencentClient + YFinanceClient honor 30d/60d/6m/1y periods"
```

---

### Task A6: `/stock/{ticker}/chart-data` JSON endpoint

**Files:**
- Modify: `marketpulse/web/routes/stock.py` (add endpoint after the existing `stock_page`)
- Test: `tests/web/test_stock.py`

This endpoint composes everything: fetches bars (with 200-day headroom for SMA200), computes all overlays via the signal-module helpers, scans signal markers, and returns a single JSON payload.

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_stock.py` (create login helpers if not present — copy `_login` pattern from `tests/web/test_trades.py`):

```python
from datetime import UTC, date, datetime, timedelta

from marketpulse.data.types import Bar, Quote


def _make_bars(n: int, start_close: float = 100.0) -> list[Bar]:
    today = date.today()
    return [
        Bar(date=today - timedelta(days=n - i),
            open=start_close + i, high=start_close + i + 1,
            low=start_close + i - 1, close=start_close + i,
            volume=1_000_000)
        for i in range(n)
    ]


class _FakeData:
    def __init__(self, bars: list[Bar]):
        self.bars = bars
        self.last_period: str | None = None

    def get_quote(self, ticker):
        return Quote(ticker=ticker, price=100.0, change_pct=0,
                     volume=1, avg_volume_20d=1, fetched_at=datetime.now(UTC))

    def get_history(self, ticker, period="60d"):
        self.last_period = period
        return self.bars

    def get_news(self, ticker, limit=10): return []


def test_chart_data_returns_expected_keys(client, monkeypatch):
    _login(client, monkeypatch)
    fake = _FakeData(_make_bars(300))
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        r = client.get("/stock/AAPL/chart-data?period=60d")
        assert r.status_code == 200
        data = r.json()
        assert set(data.keys()) >= {
            "bars", "ema12", "ema26", "sma50", "sma200",
            "bb_upper", "bb_middle", "bb_lower",
            "rsi", "macd", "signal_markers",
        }
        assert isinstance(data["bars"], list)
        assert data["bars"][0].keys() >= {"time", "open", "high", "low", "close", "volume"}
        assert isinstance(data["macd"], dict)
        assert set(data["macd"].keys()) == {"line", "signal", "histogram"}
    finally:
        client.app.dependency_overrides.clear()


def test_chart_data_fetches_with_200d_headroom_for_sma(client, monkeypatch):
    _login(client, monkeypatch)
    fake = _FakeData(_make_bars(300))
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        client.get("/stock/AAPL/chart-data?period=30d")
        # Despite user requesting 30d, backend should fetch 1y (largest that covers
        # SMA200 + 30d visible). This keeps cache utilization simple.
        assert fake.last_period == "1y"
    finally:
        client.app.dependency_overrides.clear()


def test_chart_data_unknown_period_returns_422(client, monkeypatch):
    _login(client, monkeypatch)
    fake = _FakeData(_make_bars(10))
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        r = client.get("/stock/AAPL/chart-data?period=banana")
        assert r.status_code == 422
    finally:
        client.app.dependency_overrides.clear()


def test_chart_data_empty_bars_returns_empty_arrays(client, monkeypatch):
    _login(client, monkeypatch)
    fake = _FakeData([])
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        r = client.get("/stock/AAPL/chart-data?period=60d")
        assert r.status_code == 200
        data = r.json()
        assert data["bars"] == []
        assert data["ema12"] == []
        assert data["signal_markers"] == []
    finally:
        client.app.dependency_overrides.clear()


def test_chart_data_sets_cache_control_header(client, monkeypatch):
    _login(client, monkeypatch)
    fake = _FakeData(_make_bars(10))
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: fake
    try:
        r = client.get("/stock/AAPL/chart-data?period=60d")
        assert "max-age=300" in r.headers.get("cache-control", "")
    finally:
        client.app.dependency_overrides.clear()
```

If `_login` isn't already in `tests/web/test_stock.py`, copy this at the top of the file (mirrors `tests/web/test_trades.py`):

```python
from marketpulse.auth.password import hash_password


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/web/test_stock.py -v -k chart_data`
Expected: all FAIL with 404 (route doesn't exist)

- [ ] **Step 3: Add the endpoint to `marketpulse/web/routes/stock.py`**

At the top of the file, expand imports:

```python
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
```

Add new endpoint after the existing `stock_page` function:

```python
_VALID_PERIODS = {"30d", "60d", "6m", "1y"}
_PERIOD_DAYS = {"30d": 30, "60d": 60, "6m": 180, "1y": 365}


@router.get("/stock/{ticker}/chart-data")
def stock_chart_data(
    ticker: str,
    period: str = Query("60d"),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    if period not in _VALID_PERIODS:
        raise HTTPException(
            status_code=422,
            detail=f"period must be one of {sorted(_VALID_PERIODS)}",
        )
    ticker = ticker.upper()
    # Always fetch 1y so we have SMA200 headroom regardless of visible period.
    # Trimming by date happens client-side via the cutoff below.
    try:
        all_bars = data.get_history(ticker, period="1y")
    except Exception as exc:
        log.warning("chart_data_history_failed", ticker=ticker, error=str(exc))
        all_bars = []

    cutoff = date.today() - timedelta(days=_PERIOD_DAYS[period])

    # Build response — empty result is a valid response, not an error.
    if not all_bars:
        empty: list = []
        payload = {
            "bars": empty, "ema12": empty, "ema26": empty,
            "sma50": empty, "sma200": empty,
            "bb_upper": empty, "bb_middle": empty, "bb_lower": empty,
            "rsi": empty,
            "macd": {"line": empty, "signal": empty, "histogram": empty},
            "signal_markers": empty,
        }
        return JSONResponse(payload, headers={"Cache-Control": "private, max-age=300"})

    closes = [b.close for b in all_bars]

    # Compute all overlays on the full series.
    from marketpulse.recap.signals import (
        BB_PERIOD, BB_STD_DEV, RSI_PERIOD,
        ema, macd as macd_fn, scan_signal_markers, sma,
    )
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)

    # Bollinger over the full series: rolling SMA20 ± 2σ
    bb_upper: list[float | None] = []
    bb_middle: list[float | None] = []
    bb_lower: list[float | None] = []
    for i in range(len(closes)):
        if i + 1 < BB_PERIOD:
            bb_upper.append(None); bb_middle.append(None); bb_lower.append(None)
            continue
        window = closes[i - BB_PERIOD + 1 : i + 1]
        m = sum(window) / BB_PERIOD
        var = sum((x - m) ** 2 for x in window) / BB_PERIOD
        std = var ** 0.5
        bb_middle.append(m)
        bb_upper.append(m + BB_STD_DEV * std)
        bb_lower.append(m - BB_STD_DEV * std)

    # Rolling RSI (Wilder) across the series
    rsi_series: list[float | None] = [None] * len(closes)
    if len(closes) >= RSI_PERIOD + 1:
        deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
        gains = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]
        avg_g = sum(gains[:RSI_PERIOD]) / RSI_PERIOD
        avg_l = sum(losses[:RSI_PERIOD]) / RSI_PERIOD
        if avg_l == 0 and avg_g == 0:
            rsi_series[RSI_PERIOD] = None
        elif avg_l == 0:
            rsi_series[RSI_PERIOD] = 100.0
        else:
            rs = avg_g / avg_l
            rsi_series[RSI_PERIOD] = 100.0 - (100.0 / (1 + rs))
        for i in range(RSI_PERIOD, len(deltas)):
            avg_g = (avg_g * (RSI_PERIOD - 1) + gains[i]) / RSI_PERIOD
            avg_l = (avg_l * (RSI_PERIOD - 1) + losses[i]) / RSI_PERIOD
            if avg_l == 0 and avg_g == 0:
                rsi_series[i + 1] = None
            elif avg_l == 0:
                rsi_series[i + 1] = 100.0
            else:
                rs = avg_g / avg_l
                rsi_series[i + 1] = 100.0 - (100.0 / (1 + rs))

    macd_line, macd_signal, macd_hist = macd_fn(closes)
    markers = scan_signal_markers(all_bars)

    # Helper: convert (bars, series) to list of {time, value} dicts, trimming
    # to visible window (date >= cutoff) and dropping leading Nones.
    def series_after(bars, series):
        out = []
        for b, v in zip(bars, series, strict=True):
            if b.date < cutoff:
                continue
            out.append({"time": b.date.isoformat(), "value": v})
        return out

    visible_bars = [b for b in all_bars if b.date >= cutoff]
    payload = {
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
        "rsi": series_after(all_bars, rsi_series),
        "macd": {
            "line": series_after(all_bars, macd_line),
            "signal": series_after(all_bars, macd_signal),
            "histogram": series_after(all_bars, macd_hist),
        },
        "signal_markers": [m for m in markers if m["time"] >= cutoff.isoformat()],
    }
    return JSONResponse(payload, headers={"Cache-Control": "private, max-age=300"})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/web/test_stock.py -v -k chart_data`
Expected: all 5 chart_data tests PASS

Run full suite:
Run: `.venv/bin/pytest tests/ -q`
Expected: 150+ passed

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_stock.py marketpulse/web/routes/stock.py
git commit -m "feat(stock): GET /stock/{ticker}/chart-data JSON endpoint with all indicators"
```

---

### Task A7: Frontend chart.js + stock.html DOM

**Files:**
- Create: `marketpulse/web/static/chart.js`
- Modify: `marketpulse/web/templates/stock.html` (add chart DOM + script tag)
- Test: manual smoke (deferred until after deploy)

Lightweight-charts v4 UMD is loaded from jsDelivr. The script reads the current ticker from a data attribute on the chart container, fetches data, and wires up range buttons.

- [ ] **Step 1: Create `marketpulse/web/static/chart.js`**

```javascript
// MarketPulse stock detail K-line chart.
// Uses TradingView lightweight-charts v4 loaded from jsDelivr.
// Reads ticker from <div id="chart-main" data-ticker="AAPL">.

(function () {
  const SIGNAL_STYLES = {
    ema_golden_cross:  { shape: "arrowUp",   color: "#16a34a", text: "金叉" },
    ema_death_cross:   { shape: "arrowDown", color: "#dc2626", text: "死叉" },
    rsi_overbought:    { shape: "circle",    color: "#f59e0b", text: "超买" },
    rsi_oversold:      { shape: "circle",    color: "#3b82f6", text: "超卖" },
    bollinger_upper:   { shape: "square",    color: "#a855f7", text: "上轨" },
    bollinger_lower:   { shape: "square",    color: "#6366f1", text: "下轨" },
  };

  function densify(series) {
    // Drop entries where value is null; lightweight-charts ignores them anyway,
    // but explicit filtering keeps the API surface small.
    return series.filter(p => p.value !== null && p.value !== undefined);
  }

  function renderCharts(payload) {
    // Clear any previous instances.
    document.getElementById("chart-main").innerHTML = "";
    document.getElementById("chart-rsi").innerHTML = "";
    document.getElementById("chart-macd").innerHTML = "";

    if (!payload.bars || payload.bars.length === 0) {
      document.getElementById("chart-main").innerHTML =
        '<p class="text-slate-500 text-sm py-8 text-center">暂无 K 线数据</p>';
      return;
    }

    const commonOpts = {
      layout: { background: { color: "#ffffff" }, textColor: "#334155" },
      grid: { vertLines: { color: "#e2e8f0" }, horzLines: { color: "#e2e8f0" } },
      timeScale: { borderColor: "#cbd5e1" },
    };

    // === Main chart: candles + EMA/SMA + Bollinger + volume ===
    const mainChart = LightweightCharts.createChart(
      document.getElementById("chart-main"),
      Object.assign({ height: 400 }, commonOpts),
    );
    const candleSeries = mainChart.addCandlestickSeries({
      upColor: "#16a34a", downColor: "#dc2626",
      borderVisible: false, wickUpColor: "#16a34a", wickDownColor: "#dc2626",
    });
    candleSeries.setData(payload.bars);

    function addLineIfData(series, opts) {
      const data = densify(series);
      if (data.length === 0) return;
      const line = mainChart.addLineSeries(opts);
      line.setData(data);
    }
    addLineIfData(payload.ema12,    { color: "#0ea5e9", lineWidth: 1, title: "EMA12" });
    addLineIfData(payload.ema26,    { color: "#f59e0b", lineWidth: 1, title: "EMA26" });
    addLineIfData(payload.sma50,    { color: "#8b5cf6", lineWidth: 1, title: "SMA50" });
    addLineIfData(payload.sma200,   { color: "#64748b", lineWidth: 1, title: "SMA200" });
    addLineIfData(payload.bb_upper, { color: "#a855f7", lineWidth: 1, lineStyle: 2, title: "BB上轨" });
    addLineIfData(payload.bb_lower, { color: "#a855f7", lineWidth: 1, lineStyle: 2, title: "BB下轨" });

    // Volume as histogram in a separate overlay pane.
    const volSeries = mainChart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
      scaleMargins: { top: 0.85, bottom: 0 },
    });
    volSeries.setData(payload.bars.map(b => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? "rgba(22,163,74,0.4)" : "rgba(220,38,38,0.4)",
    })));

    // Signal markers on the candle series.
    if (payload.signal_markers && payload.signal_markers.length > 0) {
      const markers = payload.signal_markers.map(m => {
        const style = SIGNAL_STYLES[m.type] || { shape: "circle", color: "#475569", text: m.type };
        return {
          time: m.time, position: "aboveBar",
          color: style.color, shape: style.shape, text: style.text,
        };
      });
      candleSeries.setMarkers(markers);
    }

    // === RSI pane ===
    const rsiData = densify(payload.rsi);
    if (rsiData.length > 0) {
      const rsiChart = LightweightCharts.createChart(
        document.getElementById("chart-rsi"),
        Object.assign({ height: 120 }, commonOpts),
      );
      const rsiSeries = rsiChart.addLineSeries({ color: "#9333ea", lineWidth: 1 });
      rsiSeries.setData(rsiData);
      const ob = rsiChart.addLineSeries({ color: "#fca5a5", lineWidth: 1, lineStyle: 2 });
      ob.setData(rsiData.map(p => ({ time: p.time, value: 70 })));
      const os = rsiChart.addLineSeries({ color: "#93c5fd", lineWidth: 1, lineStyle: 2 });
      os.setData(rsiData.map(p => ({ time: p.time, value: 30 })));
      mainChart.timeScale().subscribeVisibleTimeRangeChange(r => r && rsiChart.timeScale().setVisibleRange(r));
    }

    // === MACD pane ===
    const macdLine = densify(payload.macd.line);
    if (macdLine.length > 0) {
      const macdChart = LightweightCharts.createChart(
        document.getElementById("chart-macd"),
        Object.assign({ height: 120 }, commonOpts),
      );
      const line = macdChart.addLineSeries({ color: "#0ea5e9", lineWidth: 1 });
      line.setData(macdLine);
      const sig = macdChart.addLineSeries({ color: "#f59e0b", lineWidth: 1 });
      sig.setData(densify(payload.macd.signal));
      const hist = macdChart.addHistogramSeries();
      hist.setData(densify(payload.macd.histogram).map(p => ({
        time: p.time, value: p.value,
        color: p.value >= 0 ? "rgba(22,163,74,0.6)" : "rgba(220,38,38,0.6)",
      })));
      mainChart.timeScale().subscribeVisibleTimeRangeChange(r => r && macdChart.timeScale().setVisibleRange(r));
    }
  }

  async function load(ticker, period) {
    const r = await fetch(`/stock/${ticker}/chart-data?period=${period}`);
    if (!r.ok) {
      document.getElementById("chart-main").innerHTML =
        `<p class="text-red-600 text-sm py-8 text-center">加载失败: ${r.status}</p>`;
      return;
    }
    renderCharts(await r.json());
  }

  document.addEventListener("DOMContentLoaded", () => {
    const main = document.getElementById("chart-main");
    if (!main) return;
    const ticker = main.dataset.ticker;
    let currentPeriod = "60d";
    load(ticker, currentPeriod);

    document.querySelectorAll("[data-period]").forEach(btn => {
      btn.addEventListener("click", () => {
        currentPeriod = btn.dataset.period;
        document.querySelectorAll("[data-period]").forEach(b =>
          b.classList.toggle("bg-slate-900", b === btn) ||
          b.classList.toggle("text-white", b === btn));
        load(ticker, currentPeriod);
      });
    });
  });
})();
```

- [ ] **Step 2: Modify `marketpulse/web/templates/stock.html`**

Replace the file with:

```html
{% extends "base.html" %}
{% block content %}
<section class="bg-white rounded-md shadow-sm p-4">
  <header class="flex items-baseline justify-between">
    <h1 class="text-xl font-semibold">{{ ticker }}</h1>
    <span class="text-2xl">{{ "%.2f"|format(quote.price) }}
      <span class="{% if quote.change_pct >= 0 %}text-green-600{% else %}text-red-600{% endif %}">
        {{ "%+.2f"|format(quote.change_pct) }}%
      </span>
    </span>
  </header>
  {% if quote.stale %}<p class="text-amber-600 text-xs">⚠ 数据来自缓存(非实时)</p>{% endif %}

  <div class="flex gap-1 mt-4 text-xs">
    <button data-period="30d" class="px-2 py-1 rounded border border-slate-200">30D</button>
    <button data-period="60d" class="px-2 py-1 rounded border border-slate-200 bg-slate-900 text-white">60D</button>
    <button data-period="6m"  class="px-2 py-1 rounded border border-slate-200">6M</button>
    <button data-period="1y"  class="px-2 py-1 rounded border border-slate-200">1Y</button>
  </div>

  <div id="chart-main"  data-ticker="{{ ticker }}" class="mt-2"></div>
  <div id="chart-rsi"   class="mt-2"></div>
  <div id="chart-macd"  class="mt-2"></div>

  <h2 class="mt-6 font-semibold">最新新闻</h2>
  <ul class="mt-2 text-sm space-y-1">
    {% for n in news %}
    <li><a href="{{ n.url }}" class="text-blue-600">{{ n.headline }}</a>
        <span class="text-slate-500">— {{ n.source }}</span></li>
    {% endfor %}
  </ul>

  <div id="analysis" class="mt-6">
    <button hx-post="/stock/{{ ticker }}/analyze" hx-target="#analysis" hx-swap="innerHTML"
            class="bg-slate-900 text-white px-3 py-1 rounded">AI 深度分析</button>
  </div>
</section>

<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.2/dist/lightweight-charts.standalone.production.js"></script>
<script src="/static/chart.js"></script>
{% endblock %}
```

- [ ] **Step 3: Smoke-check route still serves the page**

Run: `.venv/bin/pytest tests/web/test_stock.py -v`
Expected: existing stock_page tests still PASS

- [ ] **Step 4: Commit**

```bash
git add marketpulse/web/static/chart.js marketpulse/web/templates/stock.html
git commit -m "feat(stock): K-line chart UI with lightweight-charts + range selector"
```

- [ ] **Step 5: After deploy — manual smoke**

After GitHub Actions deploys this, open `/stock/QUBT` in browser. Verify:
- Candlestick chart renders
- EMA12/EMA26/SMA50/SMA200/BB upper/lower lines visible
- Volume histogram below candles
- RSI pane shows oscillator + 30/70 reference lines
- MACD pane shows line + signal + histogram
- Signal markers (arrows/dots) appear on candles
- Clicking 30D / 60D / 6M / 1Y buttons reloads the chart data

If any panel is broken, check browser console for JS errors before filing a fix.

---

## Phase B — Daily Recap Push

### Task B1: Config — `notifier_recap_enabled` and `public_base_url`

**Files:**
- Modify: `marketpulse/config.py`
- Test: `tests/unit/test_config.py` (create if missing)

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_config.py` (create if not present):

```python
import pytest


def test_notifier_recap_enabled_default_true(monkeypatch):
    monkeypatch.delenv("NOTIFIER_RECAP_ENABLED", raising=False)
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    s = get_settings()
    assert s.notifier_recap_enabled is True


def test_notifier_recap_enabled_can_disable(monkeypatch):
    monkeypatch.setenv("NOTIFIER_RECAP_ENABLED", "false")
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    assert get_settings().notifier_recap_enabled is False


def test_public_base_url_default_empty(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    assert get_settings().public_base_url == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_config.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'notifier_recap_enabled'`

- [ ] **Step 3: Add fields to `marketpulse/config.py`**

In the `Settings` class (near the other notifier fields), add:

```python
notifier_recap_enabled: bool = Field(True, alias="NOTIFIER_RECAP_ENABLED")
public_base_url: str = Field("", alias="PUBLIC_BASE_URL")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Update `.env.example` and compose files**

Append to `.env.example`:

```
# Daily recap push (uses the same notifier as alerts above)
NOTIFIER_RECAP_ENABLED=true
# Optional — adds a "详情" link in pushed summaries pointing back to the web UI
PUBLIC_BASE_URL=
```

Append to `docker-compose.prod.yml` and `docker-compose.cn.yml` `environment:` blocks (both files):

```yaml
      NOTIFIER_RECAP_ENABLED: ${NOTIFIER_RECAP_ENABLED:-true}
      PUBLIC_BASE_URL: ${PUBLIC_BASE_URL:-}
```

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_config.py marketpulse/config.py .env.example docker-compose.prod.yml docker-compose.cn.yml
git commit -m "feat(config): NOTIFIER_RECAP_ENABLED + PUBLIC_BASE_URL"
```

---

### Task B2: `build_summary()` — recap → Markdown text

**Files:**
- Create: `marketpulse/recap/push.py`
- Test: `tests/unit/test_recap_push.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recap_push.py`:

```python
import json
from datetime import UTC, date, datetime

import pytest

from marketpulse.db.models import DailyRecap
from marketpulse.recap.push import build_summary


def _recap(**overrides) -> DailyRecap:
    r = DailyRecap(
        recap_date=date(2026, 5, 10),
        market_summary_json=json.dumps({"spy": 0.45, "qqq": -0.30, "dia": 0.10, "vix": 18.2}),
        watchlist_performance_json=json.dumps([
            {"ticker": "AAPL", "signals": ["EMA_GOLDEN_CROSS"]},
            {"ticker": "NVDA", "signals": ["RSI_OVERBOUGHT"]},
            {"ticker": "TSLA", "signals": []},
        ]),
        holdings_overview_json=json.dumps([
            {"ticker": "QUBT", "pl_pct": -21.0},
            {"ticker": "TQQQ", "pl_pct": 375.5},
        ]),
        holdings_totals_json=json.dumps({"pl_dollars": -2064.41, "pl_pct": -2.64}),
        ai_commentary_text="今日大盘震荡。持仓中 TQQQ 表现突出,QUBT 续跌需注意止损。" * 10,
        generation_status="ok",
        generated_at=datetime.now(UTC),
    )
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


def test_build_summary_has_title_and_body():
    title, body = build_summary(_recap(), base_url="https://nas.local:8088")
    assert "2026-05-10" in title
    assert "MarketPulse" in title
    assert "📈" in body or "大盘" in body
    assert "持仓" in body
    assert "AAPL" in body
    assert "https://nas.local:8088" in body


def test_build_summary_skips_missing_sections():
    r = _recap(market_summary_json=None, holdings_overview_json=None, ai_commentary_text=None)
    _, body = build_summary(r)
    # Watchlist signals still rendered
    assert "AAPL" in body
    # Holdings section absent
    assert "持仓" not in body


def test_build_summary_omits_link_when_no_base_url():
    _, body = build_summary(_recap(), base_url=None)
    assert "详情" not in body


def test_build_summary_truncates_long_ai_commentary():
    # 5000-char commentary should be cut to <=200 chars in the AI总评 section
    long_text = "测试" * 2500
    _, body = build_summary(_recap(ai_commentary_text=long_text))
    # Find the AI总评 segment
    ai_segment = body.split("🤖 AI 总评")[-1].split("───")[0]
    # Allow some slack for the truncation marker but must be way below 5000
    assert len(ai_segment.strip()) < 300


def test_build_summary_truncates_for_bark():
    # Bark limit is 4096 chars; with 50 holdings + 50 signals body should still fit
    huge_watch = json.dumps([
        {"ticker": f"T{i}", "signals": ["EMA_GOLDEN_CROSS"]} for i in range(200)
    ])
    r = _recap(watchlist_performance_json=huge_watch)
    _, body = build_summary(r, notifier_kind="bark")
    assert len(body) <= 4096


def test_build_summary_does_not_truncate_for_smtp():
    huge_watch = json.dumps([
        {"ticker": f"T{i}", "signals": ["EMA_GOLDEN_CROSS"]} for i in range(500)
    ])
    r = _recap(watchlist_performance_json=huge_watch)
    _, body = build_summary(r, notifier_kind="smtp")
    # SMTP has no limit — body can be much larger than Bark's 4096
    assert len(body) > 4096
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_recap_push.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketpulse.recap.push'`

- [ ] **Step 3: Create `marketpulse/recap/push.py`**

```python
"""Build a short Markdown summary of a DailyRecap and push it via a notifier.

The format is designed to be readable on Bark (4096-char limit), Server酱
(32 KB), and SMTP (unlimited). Empty sections are silently skipped so missing
data never produces a broken-looking message.
"""

import json
from typing import Any

from tenacity import retry, stop_after_attempt, wait_fixed

from marketpulse.alerts.notifier import Notifier
from marketpulse.db.models import DailyRecap
from marketpulse.logging import get_logger

log = get_logger(__name__)

# Per-channel body limits. SMTP is unlimited so we use a sentinel.
_BODY_LIMITS = {
    "bark": 3500,        # Bark accepts 4096; leave headroom for footer
    "serverchan": 30000, # 32 KB nominal
    "smtp": None,        # no limit
}

_SIGNAL_LABELS = {
    "EMA_GOLDEN_CROSS": "EMA 金叉",
    "EMA_DEATH_CROSS": "EMA 死叉",
    "RSI_OVERBOUGHT": "RSI 超买",
    "RSI_OVERSOLD": "RSI 超卖",
    "BOLLINGER_UPPER": "突破布林上轨",
    "BOLLINGER_LOWER": "跌破布林下轨",
    "BIG_MOVE": "大幅波动",
    "VOLUME_SPIKE": "成交量异常",
    "MA20_BREAKOUT": "突破 MA20",
}


def _truncate(text: str, limit: int | None, suffix: str = "…") -> str:
    if limit is None or len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def _market_section(market_json: str | None) -> str | None:
    if not market_json:
        return None
    d = json.loads(market_json)
    parts = []
    if "spy" in d: parts.append(f"SPY {d['spy']:+.2f}%")
    if "qqq" in d: parts.append(f"QQQ {d['qqq']:+.2f}%")
    if "dia" in d: parts.append(f"DIA {d['dia']:+.2f}%")
    if "vix" in d: parts.append(f"VIX {d['vix']:.1f}")
    return "📈 大盘\n" + "  ".join(parts) if parts else None


def _holdings_section(overview_json: str | None, totals_json: str | None) -> str | None:
    if not overview_json:
        return None
    rows = json.loads(overview_json)
    if not rows:
        return None
    head = "💼 持仓"
    if totals_json:
        t = json.loads(totals_json)
        head += f" ({t.get('pl_dollars', 0):+.0f} / {t.get('pl_pct', 0):+.2f}%)"
    body_parts = []
    for r in rows:
        ticker = r.get("ticker", "?")
        pl_pct = r.get("pl_pct")
        if pl_pct is None:
            continue
        body_parts.append(f"{ticker} {pl_pct:+.0f}%")
    if not body_parts:
        return head
    return head + "\n" + "  ".join(body_parts)


def _signals_section(perf_json: str | None) -> str | None:
    if not perf_json:
        return None
    rows = json.loads(perf_json)
    fired = []
    for r in rows:
        sigs = r.get("signals") or []
        if sigs:
            labels = ", ".join(_SIGNAL_LABELS.get(s, s) for s in sigs)
            fired.append(f"{r.get('ticker', '?')}: {labels}")
    if not fired:
        return None
    return "⚠️ 异动信号\n" + "\n".join(fired)


def _ai_section(text: str | None, max_chars: int = 200) -> str | None:
    if not text or not text.strip():
        return None
    body = text.strip()
    if len(body) > max_chars:
        body = body[: max_chars] + "…"
    return f"🤖 AI 总评\n{body}"


def build_summary(
    recap: DailyRecap,
    base_url: str | None = None,
    notifier_kind: str | None = None,
) -> tuple[str, str]:
    """Produce (title, body) for the given recap.

    `notifier_kind` is used only to size the body to the channel limit. Unknown
    or None means apply no limit (SMTP-style).
    """
    title = f"MarketPulse 复盘 · {recap.recap_date.isoformat()}"

    sections: list[str] = []
    for section in (
        _market_section(recap.market_summary_json),
        _holdings_section(recap.holdings_overview_json, recap.holdings_totals_json),
        _signals_section(recap.watchlist_performance_json),
        _ai_section(recap.ai_commentary_text),
    ):
        if section:
            sections.append(section)

    body = "\n\n".join(sections)
    if base_url:
        link = f"{base_url.rstrip('/')}/recap/{recap.recap_date.isoformat()}"
        body += f"\n\n───\n详情: {link}"

    limit = _BODY_LIMITS.get((notifier_kind or "").lower())
    body = _truncate(body, limit)
    return title, body


@retry(stop=stop_after_attempt(2), wait=wait_fixed(2), reraise=False)
def _send_with_retry(notifier: Notifier, title: str, body: str, url: str | None) -> bool:
    return notifier.send(title, body, url=url)


def push_recap_summary(
    recap: DailyRecap,
    notifier: Notifier,
    base_url: str | None = None,
    notifier_kind: str | None = None,
) -> bool:
    """Build + send the summary. Returns True on success.

    Wraps the send in tenacity (one retry after a 2s wait). Any exception that
    escapes the retry is caught and logged — recap-push failure is non-fatal.
    """
    title, body = build_summary(recap, base_url=base_url, notifier_kind=notifier_kind)
    url = (
        f"{base_url.rstrip('/')}/recap/{recap.recap_date.isoformat()}"
        if base_url else None
    )
    try:
        return bool(_send_with_retry(notifier, title, body, url))
    except Exception as exc:
        log.warning("recap_push_failed_after_retry", error=str(exc))
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_recap_push.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_recap_push.py marketpulse/recap/push.py
git commit -m "feat(recap): build_summary + push_recap_summary with retry"
```

---

### Task B3: Wire push into the daily recap scheduler hook

**Files:**
- Modify: `marketpulse/scheduler/jobs.py` (function `run_daily_recap`)
- Test: `tests/unit/test_scheduler_jobs.py` (create if missing)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_scheduler_jobs.py` (or append if present):

```python
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import pytest

from marketpulse.alerts.notifier import NoopNotifier
from marketpulse.db.models import DailyRecap


@pytest.fixture
def fake_recap():
    return DailyRecap(
        recap_date=date.today(),
        market_summary_json='{"spy": 0.5, "qqq": 0.3, "dia": 0.1, "vix": 15}',
        ai_commentary_text="今日小幅上涨。",
        generation_status="ok",
        generated_at=datetime.now(UTC),
    )


def test_recap_push_called_when_enabled(monkeypatch, fake_recap):
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y")
    monkeypatch.setenv("NOTIFIER_KIND", "bark")
    monkeypatch.setenv("NOTIFIER_BARK_URL", "https://api.day.app/abc")
    monkeypatch.setenv("NOTIFIER_RECAP_ENABLED", "true")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    with patch("marketpulse.scheduler.jobs.RecapService") as RS, \
         patch("marketpulse.scheduler.jobs.push_recap_summary") as push, \
         patch("marketpulse.scheduler.jobs.build_notifier") as bn, \
         patch("marketpulse.scheduler.jobs._build_quote_client"), \
         patch("marketpulse.scheduler.jobs.session_scope"):
        RS.return_value.generate.return_value = fake_recap
        bn.return_value = MagicMock()  # not a NoopNotifier
        from marketpulse.scheduler.jobs import run_daily_recap
        run_daily_recap()
        assert push.called


def test_recap_push_skipped_when_disabled(monkeypatch, fake_recap):
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y")
    monkeypatch.setenv("NOTIFIER_KIND", "bark")
    monkeypatch.setenv("NOTIFIER_RECAP_ENABLED", "false")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    with patch("marketpulse.scheduler.jobs.RecapService") as RS, \
         patch("marketpulse.scheduler.jobs.push_recap_summary") as push, \
         patch("marketpulse.scheduler.jobs._build_quote_client"), \
         patch("marketpulse.scheduler.jobs.session_scope"):
        RS.return_value.generate.return_value = fake_recap
        from marketpulse.scheduler.jobs import run_daily_recap
        run_daily_recap()
        assert not push.called


def test_recap_push_skipped_when_notifier_is_noop(monkeypatch, fake_recap):
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y")
    monkeypatch.setenv("NOTIFIER_KIND", "none")
    monkeypatch.setenv("NOTIFIER_RECAP_ENABLED", "true")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    with patch("marketpulse.scheduler.jobs.RecapService") as RS, \
         patch("marketpulse.scheduler.jobs.push_recap_summary") as push, \
         patch("marketpulse.scheduler.jobs.build_notifier") as bn, \
         patch("marketpulse.scheduler.jobs._build_quote_client"), \
         patch("marketpulse.scheduler.jobs.session_scope"):
        RS.return_value.generate.return_value = fake_recap
        bn.return_value = NoopNotifier()
        from marketpulse.scheduler.jobs import run_daily_recap
        run_daily_recap()
        assert not push.called


def test_recap_push_failure_does_not_propagate(monkeypatch, fake_recap):
    """If push raises (even past the internal retry), the recap job still finishes."""
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y")
    monkeypatch.setenv("NOTIFIER_KIND", "bark")
    monkeypatch.setenv("NOTIFIER_BARK_URL", "https://api.day.app/abc")
    monkeypatch.setenv("NOTIFIER_RECAP_ENABLED", "true")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    with patch("marketpulse.scheduler.jobs.RecapService") as RS, \
         patch("marketpulse.scheduler.jobs.push_recap_summary",
               side_effect=RuntimeError("boom")), \
         patch("marketpulse.scheduler.jobs.build_notifier") as bn, \
         patch("marketpulse.scheduler.jobs._build_quote_client"), \
         patch("marketpulse.scheduler.jobs.session_scope"):
        RS.return_value.generate.return_value = fake_recap
        bn.return_value = MagicMock()
        from marketpulse.scheduler.jobs import run_daily_recap
        run_daily_recap()  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_scheduler_jobs.py -v`
Expected: FAIL — `push_recap_summary` is not imported in `marketpulse.scheduler.jobs`, so the patch target doesn't exist; or `run_daily_recap` runs but never calls push.

- [ ] **Step 3: Update `marketpulse/scheduler/jobs.py`**

At the top, add the import:

```python
from marketpulse.alerts.notifier import NoopNotifier
from marketpulse.recap.push import push_recap_summary
```

Replace `run_daily_recap` with:

```python
def run_daily_recap() -> None:
    target = date.today()
    log.info("recap_job_start", date=str(target))
    settings = get_settings()
    gen = session_scope()
    db = next(gen)
    try:
        data = DataService(db, _build_quote_client(), news_ttl_days=settings.news_cache_ttl_days)
        ai = AiService(
            db, ai_client=AnthropicClient(), data=data,
            model=settings.ai_model, ttl_hours=settings.ai_cache_ttl_hours,
        )
        svc = RecapService(db, data=data, ai=ai)
        result = svc.generate(target)
        log.info("recap_job_done", date=str(target), status=result.generation_status)

        # Optional push — non-blocking, never fails the job.
        if settings.notifier_recap_enabled:
            notifier = build_notifier(settings)
            if not isinstance(notifier, NoopNotifier):
                try:
                    push_recap_summary(
                        result, notifier,
                        base_url=settings.public_base_url or None,
                        notifier_kind=settings.notifier_kind,
                    )
                except Exception as exc:
                    log.warning("recap_push_skipped", error=str(exc))
    finally:
        db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_scheduler_jobs.py -v`
Expected: all 4 tests PASS

Run full suite:
Run: `.venv/bin/pytest tests/ -q`
Expected: 160+ passed, no regressions

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_scheduler_jobs.py marketpulse/scheduler/jobs.py
git commit -m "feat(scheduler): push daily recap summary after recap completes"
```

---

### Task B4: Push deploy + manual verification

Not a code task — a release checklist after Phase B is merged.

- [ ] **Step 1: Push the branch**

```bash
git push origin main
```

- [ ] **Step 2: Wait for GitHub Actions build + Aliyun ACR push + Portainer redeploy**

```bash
gh run watch
```

- [ ] **Step 3: Verify recap push end-to-end**

On the NAS host, manually trigger a recap (the daily 16:30 ET cron will also do this naturally):

```bash
docker exec marketpulse python -c \
    "from marketpulse.scheduler.jobs import run_daily_recap; run_daily_recap()"
```

Expected:
- Bark / WeChat / email receives one message titled `MarketPulse 复盘 · YYYY-MM-DD`
- Body contains 大盘 / 持仓 / 异动 / AI 总评 sections (whichever have data)
- If `PUBLIC_BASE_URL` is set, message ends with `详情: <link>`
- Container logs show `recap_job_done` followed by no warning (or a `recap_push_skipped` warning if notifier is down)

If the push doesn't arrive, check `docker logs marketpulse | tail -50` for `notifier_*_failed` messages — same diagnostic flow as alerts.

---

## Self-Review

Verified:

- **Spec coverage:**
  - K-line chart frontend → Task A7
  - Backend chart-data endpoint → Task A6
  - SMA / MACD computations → Tasks A1, A2
  - Signal markers → Task A3
  - Multi-period support → Tasks A4, A5
  - Recap push module → Task B2
  - Scheduler hook → Task B3
  - Config additions → Task B1
  - .env.example + compose passthrough → Task B1
  - Each spec edge case has a corresponding test in one of A6 (chart-data: unknown period, empty bars, headers, headroom) or B2 (push: missing sections, link toggling, channel-specific truncation) or B3 (scheduler: enabled/disabled/noop/push-failure)

- **Type consistency:**
  - `period` strings consistent across A4/A5/A6: always `30d|60d|6m|1y`
  - `signal_marker` dict keys consistent in A3 and A6: `time`, `type`, `note`
  - `_PERIOD_DAYS` dict defined separately in service.py, tencent_client.py, and stock.py (route) — same content; could be DRY'd but kept per-module to avoid cross-package coupling
  - `notifier_kind` argument in B2's `build_summary()` and `push_recap_summary()` matches B3's call site

- **Placeholder scan:** No TBD/TODO. Every code step shows complete code or a precise file/line modification.
