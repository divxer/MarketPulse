# MarketPulse · NineScrolls Redesign v1 · Mockups

High-fidelity, wide-screen (2560 px) redesign of MarketPulse's four priority screens, applying the **NineScrolls** precision-editorial design language to a personal portfolio + AI analysis tool. The goal was TradingView-level density paired with Bloomberg-level gravitas, replacing the current `max-w-5xl` (1024 px) Tailwind layout that wastes ~60% of a 2K monitor.

## How to view

Everything lives in a single design canvas. Open `index.html` in any modern browser — no build step. The canvas pans / zooms with mouse + wheel; each artboard has a Focus button (top-right of the frame) to fullscreen it for screenshots or review.

```bash
cd docs/design/mockups
python3 -m http.server 8080
# → http://localhost:8080/
```

## File map

### Canvas + shared

| File | Role |
|---|---|
| `index.html` | Canvas shell. Wires up React + Babel, loads the panels, mounts the `DesignCanvas` with all six artboards. |
| `ns-tokens.css` | NineScrolls design tokens (color, type, spacing, radius, shadow, motion). Copied verbatim from the design system project — **do not edit here**, edit upstream. |
| `app.css` | App-specific overlay on top of `ns-tokens.css` — chart pane styles, table primitive, chip/button system, Bloomberg dark surface, AI markdown styling. |
| `assets/logo.svg` | NineScrolls dragon mark, used in the chrome wordmark lockup. |
| `design-canvas.jsx` | Pan/zoom canvas component (starter from NineScrolls). Renders `<DCSection>` / `<DCArtboard>` children. |
| `chart-data.jsx` | Deterministic OHLC/RSI/MACD/Bollinger/SMA generators — seeded PRNG so charts look real but render identically every load. |
| `chart-svg.jsx` | SVG chart primitives: `<CandleChart>`, `<RSIChart>`, `<MACDChart>`, `<Sparkline>`, `<AllocationBar>`. Both light and dark themes. |
| `shell.jsx` | Shared chrome elements: top-nav config, watchlist fixture (10 symbols with names, prices, % changes, 30-day sparkline values). |

### Stock detail · 3 variants

All three render `/stock/AAPL` with **identical fixture data** so layout decisions can be compared directly.

| File | Variant | Layout decision |
|---|---|---|
| `variant-a.jsx` | **A · TradingView** | 3-column grid: 280 px watchlist rail · flex-fill chart stack · 440 px context rail. Chart stack = top OHLC bar + K-line (520 px) + RSI (130 px) + MACD (130 px) + recent trades table. Context rail = position card → record-trade form → AI analysis → news. Top symbol strip with price, % change, OHLC quick stats, and primary actions (watch / record / AI). |
| `variant-b.jsx` | **B · Bloomberg terminal** | 4-column dense grid on near-black `#050912` surface with amber `#ffb44a` accents. Function-code panel headers (DES / GP / RSI / MACD / WL / TRDE / OMS / NEWS / AI). Top command line with `<GO>` glyph, ticker tape (DJIA · SPX · VIX · US10Y · WTI · GOLD · BTC · DXY · USDCNH), F-key status footer. Designed to read at a glance for someone with 5+ symbols on screen at once. |
| `variant-c.jsx` | **C · Modern minimal** | Full-bleed light theme. 2400 px wide K-line with **floating glass panels** (OHLC, position, inline AI insight) docked at its corners. Paired RSI / MACD cards below. Long-form AI analysis card centered with side rail (record trade · news). Recent trades full-width at the bottom. The hero treats the symbol + price like editorial typography. |

### Supporting pages

| File | Page | Layout decision |
|---|---|---|
| `page-trades.jsx` | **/trades** | 5-KPI strip (total count, YTD realised P&L, win rate, avg hold days, this month). Filter chips (all / trade / split / dividend) + type-aware add form with conditional field groups. Full ledger table with edit/delete affordances and color-coded buy/sell/split/dividend rows. Right rail: monthly realised P&L histogram, Robinhood CSV importer with drop-zone, per-ticker realised P&L leaderboard. |
| `page-holdings.jsx` | **/holdings** | Editorial hero with massive total market value + unrealised P&L. 5-KPI strip (cost · MV · unrealised P&L · realised P&L · dividends). 3-column row: allocation breakdown / sector breakdown / contributor leaderboard. Full holdings table with sparklines per row + allocation bar. Donut chart of holdings composition. Bottom: monthly P&L histogram with cumulative line overlay + AI risk analysis. |
| `page-recap.jsx` | **/recap** | Editorial long-form layout — 760 px reading column constrained for line-length readability of Chinese long-form (40–60 characters per line) + 720 px data rail. Snapshot strip of market indices (S&P · Nasdaq · Dow · VIX · US10Y) at the top. The reading column uses 17 px body, 1.85 line-height, intentional drop-cap intro paragraph, inline `<mono>` numerics with subtle highlight background. Side rail: organisation today P&L, watchlist perf table, key events, previous 5 daily recaps. |

## What's NOT here

These were intentionally scoped out and would be the next batch:

- `/watchlist`, `/alerts`, `/recaps` (index page), `/login`, `/trades/import` (full import wizard)
- Mobile (<768 px) layouts. The 2560-px-first approach means I deliberately ignored small viewports for v1.
- Dark-mode variants of A and C (B already runs dark).
- A full component library extraction. The components in these JSX files are inline; a v2 pass should hoist `<KPICard>`, `<Chrome>`, the chart panels, and the table styles into a real component module.
- Real i18n / locale handling for Chinese vs English numeric formatting. Fixture text mixes both (intentionally — matches the source repo's pattern).

## Wiring it into the real app

The mockups use **React + Babel-in-the-browser + SVG** for fast iteration. The MarketPulse production stack is **Flask + Jinja2 + HTMX + Tailwind + lightweight-charts**. Porting paths:

1. **CSS tokens** — `ns-tokens.css` can be dropped into `marketpulse/web/static/` as-is and pulled into `base.html`. The Tailwind `@theme` block (`app.src.css`) should be regenerated to mirror these tokens; alternatively run Tailwind in JIT mode against arbitrary `var(--ns-*)` values.
2. **`app.css`** — chip / button / chart-pane / table primitives are vanilla CSS, no Tailwind dependency. Drop into static and reference from `base.html`.
3. **Chart rendering** — keep `lightweight-charts` for the real K-line/RSI/MACD (production code is in `static/chart.js`). The SVG charts here are mockup fidelity only. The **light / dark theme palettes** in `chart-svg.jsx > themeColors()` should be ported to a `chart-theme.js` and consumed by `chart.js`.
4. **Layout grids** — every variant uses `display: grid` with explicit `grid-template-columns` in pixels (not Tailwind's max-width container). The Jinja templates should drop `max-w-5xl mx-auto` and adopt `grid grid-cols-[280px_1fr_440px] gap-4 px-6` (or equivalent) on the page wrapper.
5. **Bloomberg variant (B)** — load as a separate `theme=terminal` opt-in. The `--bb-*` tokens in `app.css` are scoped under `.bb-root` so they won't leak. Useful for power users who want density over polish.

## Provenance

- NineScrolls Design System: project `019e1ef1-880c-7ea6-ad42-f89a5dc63731` — `colors_and_type.css` copied as `ns-tokens.css`, `assets/logo.svg` copied as `assets/logo.svg`. Authoritative source for all `--ns-*` variables.
- Source data fixtures: built to match the schema in `marketpulse/web/templates/stock.html`, `trades.html`, `holdings.html`, `recap.html`, and the prompt versions in `marketpulse/ai/prompts.py` (`analysis-v2-zh`, `commentary-v3-zh-holdings`, `risk-v2-zh-data`).
