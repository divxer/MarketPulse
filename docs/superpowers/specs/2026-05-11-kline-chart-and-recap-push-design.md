# K-Line Chart + Daily Recap Push — Design Spec

**Status:** Approved, ready for implementation plan
**Author:** harvey
**Date:** 2026-05-11

## Goal

Two independent feature additions to MarketPulse:

- **A. K-Line Chart** on the stock detail page — render the OHLC history that the backend already fetches but currently throws away on render. Includes EMA12/26, SMA50/200, Bollinger bands, RSI, MACD, and signal-event markers. Time range selector for 30D/60D/6M/1Y.
- **B. Daily Recap Push** — when the scheduled daily recap finishes, push a ~500-char Markdown summary through the existing notifier (Bark / Server酱 / SMTP). Lets the user stay informed without opening the web UI.

The two modules are fully decoupled — neither depends on the other and they can be implemented and deployed independently.

## A. K-Line Chart

### Frontend

- Library: **TradingView lightweight-charts** v4 (UMD via jsDelivr CDN, ~50 KB)
- New asset: `marketpulse/web/static/chart.js`
- `stock.html` additions:
  - Main chart container (`#chart-main`) — candles + EMA12/EMA26/SMA50/SMA200 + Bollinger bands + signal markers
  - Volume pane (`#chart-volume`)
  - RSI pane (`#chart-rsi`) with 30/70 reference lines
  - MACD pane (`#chart-macd`) — line + signal + histogram
  - Range button group: `30D | 60D | 6M | 1Y` (default 60D)
- On page load: `fetch('/stock/{ticker}/chart-data?period=60d')` → render
- Range click: re-fetch + redraw (no full page reload)

### Backend

New endpoint:

```
GET /stock/{ticker}/chart-data?period={30d|60d|6m|1y}
```

Returns JSON:

```json
{
  "bars": [{"time": "2026-05-08", "open": 9.40, "high": 9.50, "low": 9.30, "close": 9.45, "volume": 1234567}, ...],
  "ema12": [{"time": "2026-05-08", "value": 9.42}, ...],
  "ema26": [...],
  "sma50": [...],
  "sma200": [...],
  "bb_upper": [...], "bb_middle": [...], "bb_lower": [...],
  "rsi": [{"time": "2026-05-08", "value": 65.4}, ...],
  "macd": {
    "line": [{"time": "...", "value": 0.12}, ...],
    "signal": [...],
    "histogram": [...]
  },
  "signal_markers": [
    {"time": "2026-04-20", "type": "ema_golden_cross", "note": "EMA12 crossed above EMA26"},
    {"time": "2026-05-01", "type": "rsi_overbought", "note": "RSI hit 72"}
  ]
}
```

Implementation notes:

- `marketpulse/data/service.py::get_history()` already accepts `period`; expand to accept `30d|60d|6m|1y`. Conversions:
  - `30d` → 30 calendar days
  - `60d` → 60 calendar days (current default)
  - `6m` → 180 calendar days
  - `1y` → 365 calendar days
  Tencent/yfinance return trading days only, so actual bar count will be ~70% of these.
- To compute SMA200 within a 30D-or-larger window, fetch 200 trading days of headroom from Tencent (already supports up to 640 days), compute indicators on the full series, then slice to the user-requested period before returning.
- All indicators computed server-side so the frontend stays library-only (no math).
- `signal_markers`: re-run the existing `marketpulse.recap.signals` detectors at each bar in the visible period. Emit one marker per bar where a signal first triggers (no repeated markers for consecutive same-signal bars).

  Example output for a ticker that crossed EMA golden cross on 2026-04-20 and hit overbought RSI on 2026-05-01:

  ```json
  "signal_markers": [
    {"time": "2026-04-20", "type": "ema_golden_cross",
     "note": "EMA12 ($12.30) crossed above EMA26 ($12.25)"},
    {"time": "2026-05-01", "type": "rsi_overbought",
     "note": "RSI(14) = 72.4 (≥ 70)"}
  ]
  ```

  Marker types match the existing `signals.Signal` enum: `ema_golden_cross`, `ema_death_cross`, `rsi_overbought`, `rsi_oversold`, `bollinger_upper`, `bollinger_lower`. Frontend renders each type with a distinct shape/color (triangle up/down, dot).

  Non-trading days (weekends, holidays) produce no bars and no markers. lightweight-charts compacts the time axis automatically — there's no visible gap.

### Signals module additions

In `marketpulse/recap/signals.py`:

- `sma(values: list[float], period: int) -> list[float | None]` — simple moving average, returns leading Nones when window isn't filled
- `macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[list[float|None], list[float|None], list[float|None]]` — MACD line, signal line, histogram

Both reuse the existing `ema()` helper.

### Edge cases

- Both data sources fail → empty `bars` array → frontend shows "暂无 K 线数据" placeholder
- Ticker has < 200 bars of history → `sma200` entries are `null` → frontend skips that series. Frontend rule: any indicator series where every value is `null` is not added to the chart; series with mixed real/null skip the null entries (lightweight-charts handles this natively when given a sparse array)
- Index ticker (e.g. `^VIX`) → Tencent kline rejects, HybridClient falls back to yfinance automatically
- Stale cache (`PriceCache` only has 60 days but user requested 1Y) → triggers fresh fetch from Tencent, upsert into cache for next time
- Concurrent request load: a 1Y response is ≤50 KB. Endpoint sets `Cache-Control: private, max-age=300` so the browser caches each (ticker, period) pair for 5 minutes — toggling between range buttons doesn't re-hit the backend within that window

## B. Daily Recap Push

### New module: `marketpulse/recap/push.py`

Two public functions:

```python
def build_summary(recap: DailyRecap, base_url: str | None = None) -> tuple[str, str]:
    """Returns (title, body). Title fits Bark/Server酱 title limits.
       Body ≤ 500 chars in normal case; never exceeds 3000 to stay
       within Bark's 4096-char limit."""

def push_recap_summary(recap: DailyRecap, notifier: Notifier,
                       base_url: str | None = None) -> bool:
    """Build + send. Returns True on success. Catches all notifier
       exceptions internally (logs warning). Retries once after a 2s
       wait on transient failure (uses tenacity, already a dep)."""
```

### Summary format

```
Title: MarketPulse 复盘 · 2026-05-10

Body:
📈 大盘
SPY $580.12 (+0.45%)  QQQ $510.20 (-0.30%)  VIX 18.2

💼 持仓 (-$2,064 / -2.64%)
AMSC +118%  QBTS +17%  GOOGL +23%  TQQQ +376%  TNA +74%  QUBT -21%

⚠️ 异动信号
AAPL: EMA 金叉
NVDA: RSI 超买

🤖 AI 总评
[recap.commentary_text 的前 200 字]…

详情: https://nas.tail-scale.ts.net:8088/recap/2026-05-10
```

Sections appear in this order. Each section is skipped if its data is missing:

- 大盘 skipped if `recap.market_summary_json` is null
- 持仓 skipped if no holdings
- 异动信号 skipped if no signals fired today
- AI 总评 skipped if `commentary_text` is null or empty
- 详情 link skipped if `PUBLIC_BASE_URL` is unset

### Scheduler integration

In `marketpulse/scheduler/jobs.py::run_daily_recap`, after the recap is committed to the DB:

```python
recap_obj = recap_service.run_today()
# ... existing logic ...

if settings.notifier_recap_enabled:
    notifier = build_notifier(settings)
    if not isinstance(notifier, NoopNotifier):
        try:
            push_recap_summary(recap_obj, notifier, base_url=settings.public_base_url)
        except Exception as exc:
            log.warning("recap_push_failed", error=str(exc))
```

The recap is already written to DB before this runs — push failure cannot lose the recap.

### Configuration

Added to `marketpulse/config.py`:

| Env var | Default | Description |
|---|---|---|
| `NOTIFIER_RECAP_ENABLED` | `true` | Set `false` to disable push even when notifier is configured |
| `PUBLIC_BASE_URL` | (empty) | Used in summary footer to link back to web UI. Empty → no link |

Both also added to `.env.example`, `docker-compose.prod.yml`, `docker-compose.cn.yml` with passthrough defaults.

### Edge cases

- `NOTIFIER_KIND=none` → push silently skipped (NoopNotifier check)
- Notifier raises → logged as warning, recap row stays committed
- Same-day double execution (rare) → both pushes go out; user sees an updated message rather than missing one
- Title or body exceeds channel limits → truncate with `…` and append a "see full recap at <link>". Channel limits used by the truncator:
  - **Bark**: title 256 chars, body 4096 chars (truncate to 3500 for safety + footer)
  - **Server酱**: title 32 chars, body 32 KB (rarely an issue)
  - **SMTP**: no limit (no truncation)
  The notifier exposes its identity via `notifier.kind`; truncator picks limits from a lookup table.

## Testing

| File | Coverage |
|---|---|
| `tests/unit/test_signals.py` (extend) | New `sma()` and `macd()` functions: correctness against hand-computed values, leading-None handling, period > input length |
| `tests/unit/test_recap_push.py` (new) | `build_summary()` with all section permutations: full recap, missing market data, empty holdings, no AI commentary, no signals, with/without base_url, character-limit truncation |
| `tests/web/test_stock.py` (extend) | `GET /stock/{ticker}/chart-data` returns expected JSON shape; period parameter is honored; unknown ticker returns reasonable error; empty history returns empty arrays not 500 |
| `tests/unit/test_scheduler_jobs.py` (extend) | Recap push hook: called when notifier is active; not called when `NOTIFIER_KIND=none`; not called when `NOTIFIER_RECAP_ENABLED=false`; notifier exception does not propagate out of `run_daily_recap`; persistent failure (mock raises both attempts) logs warning and recap row is still in DB |

Manual verification post-deploy:

1. Open `/stock/QUBT` — see candlestick chart with all overlays. Toggle 30D/60D/6M/1Y, data updates and redraws.
2. Trigger `run_daily_recap` manually (or wait for 16:30 ET) — receive Bark/Server酱 push within seconds of recap completion.

## File Manifest

**New:**

- `marketpulse/recap/push.py`
- `marketpulse/web/static/chart.js`
- `tests/unit/test_recap_push.py`

**Modified:**

- `marketpulse/recap/signals.py` — add `sma()`, `macd()`
- `marketpulse/data/service.py` — `get_history()` accepts any of 30d/60d/6m/1y
- `marketpulse/web/routes/stock.py` — add `/stock/{ticker}/chart-data` endpoint
- `marketpulse/web/templates/stock.html` — chart DOM + script include
- `marketpulse/scheduler/jobs.py` — recap push hook
- `marketpulse/config.py` — `notifier_recap_enabled`, `public_base_url`
- `.env.example`, `docker-compose.prod.yml`, `docker-compose.cn.yml` — new env vars
- `tests/unit/test_signals.py`, `tests/web/test_stock.py`, `tests/unit/test_scheduler_jobs.py`

## Out of Scope

Deferred to v2 (called out in the review):

- Adding MACD signals to the alert engine (just rendered on chart, not used for notifications)
- Customizable indicator periods (always 12/26/50/200, fixed for v1)
- Saving user's preferred range across sessions
- Push channel selection per-message (recap and alerts share the single configured notifier)
- Intraday / real-time chart updates via WebSocket — current model is page-load refresh only
- Per-user push preferences and multi-language summary content
- Escalation/paging on persistent notifier failure (current model: 1 retry then log; investigate via NAS logs if it happens repeatedly)
- Automated UI / visual-regression tests for chart rendering across screen sizes (manual verification only for v1)
