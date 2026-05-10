# MarketPulse v1 Design

**Date:** 2026-05-10
**Status:** Approved (brainstorming phase)

## Purpose

A self-hosted US stock monitoring assistant for personal use. v1 focuses on watchlist management, automated daily recaps, and on-demand AI analysis. Real-time streaming, intraday decision support, and portfolio P&L are explicitly deferred to later versions.

## Roadmap Context

The user's eventual goal is a 4-pillar product: real-time alerts (A), intraday decision support (B), daily recap (C), and AI analysis (D). v1 delivers a C+D MVP because:

- C and D run on free, end-of-day data (yfinance) — no real-time infrastructure or paid feeds
- C forces the watchlist/holdings model that A/B will reuse
- D layers cheaply on top of data already collected for C

v2 will add the real-time layer (A) and intraday tooling (B) on top of v1's foundation.

## Scope

### In Scope (v1)

- **Watchlist management** — add/remove/edit tickers with notes
- **Automated daily recap** generated after US market close, containing:
  - Market overview (SPY, QQQ, DIA, VIX)
  - Per-watchlist-ticker daily performance with anomaly signals (>5% move, >2x avg volume, 20/50-day MA breakout)
  - Aggregated news per ticker (yfinance news feed)
  - AI-generated commentary paragraph
- **On-demand AI deep analysis** — for any ticker: pull recent prices, fundamentals, and news; send to Claude; return structured analysis (fundamental + technical + risk); cache 24h
- **Historical recap browsing**
- **Single-user password auth**

### Out of Scope (v1)

- Real-time price streaming, WebSockets, price alerts (deferred to v2 pillar A)
- Holdings/P&L tracking (deferred to v1.1)
- Intraday decision support (deferred to v2 pillar B)
- Multi-user, subscriptions, payments
- Mobile native apps

### Data Latency

yfinance provides 15-minute delayed quotes. This is acceptable for recap + on-demand analysis. The real-time layer in v2 will require a different data source (Polygon, Alpaca, or similar).

## Architecture

### Approach: Single Monolithic Service

Single FastAPI process containing web routes, in-process scheduler, and SQLite. Packaged as one Docker image, deployed to Fly.io with a persistent volume.

Rationale: simplest deployment for self-use; clean module boundaries enable later extraction (e.g., separating the worker process) without rewrite.

### Tech Stack

- **Language/Framework:** Python 3.12, FastAPI
- **Database:** SQLite via SQLAlchemy 2.x; Alembic for migrations
- **Frontend:** Jinja2 server-side rendering + HTMX + Tailwind (no SPA)
- **Scheduler:** APScheduler (in-process background thread)
- **Data source:** yfinance (quotes, news, fundamentals)
- **LLM:** Anthropic Claude API, default model `claude-sonnet-4-6`, with prompt caching
- **Auth:** Single-user password (bcrypt hash) + signed session cookie via `itsdangerous`
- **Dependency management:** uv
- **Deployment:** Single Docker image on Fly.io with 1GB persistent volume mounted at `/data`

HTMX chosen over React because v1 features (tables, forms, recap cards) are HTMX's sweet spot, the user is the only consumer, and a future v2 real-time dashboard can be built as a standalone React page if needed without rewriting v1.

### Process Structure

```
FastAPI process
├─ Web routes (HTMX pages + JSON helpers)
├─ APScheduler (background thread)
│   ├─ 16:30 ET daily: generate recap
│   └─ Sunday weekly: clean expired caches
└─ SQLite at /data/marketpulse.db
```

### Module Layout

```
marketpulse/
├─ web/          FastAPI routes, Jinja2 templates, static assets
├─ data/         yfinance wrapper + price/news cache layer
├─ recap/        Daily recap generator
├─ ai/           Claude client, prompt templates, analysis cache
├─ db/           SQLAlchemy models, Alembic migrations
├─ scheduler/    APScheduler setup and job registration
├─ auth/         Password verify, session middleware
└─ config.py     pydantic-settings, env-driven
```

### Module Boundaries

- `data/` is the **only** module that calls yfinance. Replacing it with Polygon/Alpaca in v2 touches one module.
- `ai/` is the **only** module that calls Anthropic. Prompt changes and model swaps are localized here.
- `recap/` and `web/` consume `data/` and `ai/` through their public functions, never reaching past them.

Each module exposes a small, named surface (e.g. `data.get_quote(ticker)`, `ai.analyze(ticker)`). Internals can be refactored without disturbing consumers.

## Data Model (SQLite)

```
watchlist_items
  id (PK), ticker (UNIQUE), added_at, notes, sort_order

daily_recaps
  id (PK), recap_date (UNIQUE), market_summary_json,
  watchlist_performance_json, news_summary_json,
  ai_commentary_text, generated_at,
  generation_status   -- pending | success | failed
  error_message       -- nullable, populated on failure

ai_analyses
  id (PK), ticker, model, prompt_version,
  input_data_json,    -- snapshot of price/news fed to LLM (for reproducibility)
  response_markdown, requested_at, expires_at
  INDEX (ticker, expires_at)

price_cache
  ticker, date, open, high, low, close, volume, fetched_at
  PRIMARY KEY (ticker, date)

news_cache
  id (PK), ticker, headline, url, published_at, source, summary
  INDEX (ticker, published_at)
  -- TTL: 7 days

app_settings
  key (PK), value     -- password hash, schema version, etc.
```

## External Integrations

### `data/` Public Interface

- `get_quote(ticker) -> Quote` — current price, change %, volume (15-min delayed)
- `get_history(ticker, period) -> list[Bar]` — OHLCV; reads `price_cache` first, fetches missing from yfinance
- `get_news(ticker, limit) -> list[NewsItem]` — yfinance news, written through `news_cache`, 7-day expiry
- `get_fundamentals(ticker) -> Fundamentals` — PE, market cap, EPS, etc.
- `get_market_overview() -> MarketOverview` — SPY/QQQ/DIA/VIX in one call

**Failure handling:** retry with exponential backoff (max 3); on persistent failure return cached data flagged stale, never raise to UI without a degradation path.

### `ai/` Public Interface

- `analyze(ticker) -> AnalysisResult` — orchestrates: gather context via `data/` → render prompt → call Claude → parse → write `ai_analyses`
- `daily_commentary(recap_data) -> str` — short paragraph summarizing the day for the recap

**Prompt versioning:** every prompt template carries a `prompt_version` constant; cached results with stale versions are treated as expired and regenerated on next request.

**Prompt caching:** Anthropic prompt caching enabled for the system prompt and the (large, mostly stable) financial-data formatting instructions. Per-request user content is not cached.

### `recap/` Public Interface

- `generate_daily_recap(date) -> RecapResult`
  1. Fetch market overview
  2. For each watchlist ticker: quote + signal computation (>5% move, >2x avg volume, MA breakout)
  3. Fetch and dedupe news per ticker
  4. Call `ai.daily_commentary` with assembled data
  5. Upsert `daily_recaps` row

**Idempotency:** same-date reruns update the existing row (no duplicate inserts). Failed runs leave `generation_status='failed'` and `error_message` populated; the next 17:00 ET retry attempts again. After two failures the day is left for manual retry from the UI.

## Pages and Routes

```
GET  /login                   Login page
POST /login                   Submit password
GET  /                        Home: today's recap + watchlist table
GET  /watchlist               Watchlist management
POST /watchlist               Add ticker
DELETE /watchlist/{id}        Remove ticker
GET  /stock/{ticker}          Per-stock detail: quote, history chart, AI button
POST /stock/{ticker}/analyze  Trigger AI analysis (HTMX SSE response)
GET  /recap/{date}            Specific historical recap
GET  /recaps                  Recap list
GET  /health                  Health check (DB + scheduler status)
```

### Home Page Layout (most-used view)

```
┌─────────────────────────────────────────┐
│  Market   SPY +0.8%  QQQ +1.2%  VIX 14  │
├─────────────────────────────────────────┤
│  Today's recap (2026-05-09)             │
│  AI commentary: …                       │
│  [Expand full recap]                    │
├─────────────────────────────────────────┤
│  Watchlist          Δ%    Vol    Signal │
│  AAPL   $185.20    +1.2%  1.3x   —      │
│  NVDA   $920.50    +3.5%  2.1x   ⚡MA   │
│  TSLA   $245.10    -2.1%  0.9x   —      │
│  [+ Add]                                │
└─────────────────────────────────────────┘
```

Clicking a row opens the per-stock page.

## Key Flows

### Flow 1: Daily Automated Recap

```
APScheduler 16:30 ET
  → recap.generate_daily_recap(today)
  → upsert daily_recaps (status=success|failed)
  → on failure, scheduler retries once at 17:00
User opens /
  → reads daily_recaps WHERE date=today
  → if generation pending, page polls via HTMX every 10s until status changes
  → if failed, shows "[Retry]" button posting to manual rerun route
```

### Flow 2: On-Demand AI Analysis

```
User clicks "Deep Analysis" on /stock/NVDA
  → POST /stock/NVDA/analyze
  → check ai_analyses cache (expires_at > now AND prompt_version matches)
    → cache hit: return cached markdown
    → cache miss: gather context → stream Claude response via SSE → swap markdown into page → persist to ai_analyses
```

### Flow 3: Add Watchlist Ticker

```
HTMX form POST /watchlist with ticker
  → validate via data.get_quote (must resolve)
  → insert watchlist_items
  → return rendered table-row HTML fragment
  → HTMX swaps it into the table
On invalid ticker: return 422 with error fragment swapped above the form
```

### Error Handling Principles

- yfinance failure → UI shows "Data unavailable, showing cache from X minutes ago"
- Claude failure → recap commentary shows "AI commentary failed [Retry]"; on-demand analysis shows error with retry
- Recap job failure → `generation_status='failed'`, surfaced on home page with manual retry button
- **No silent failures.** Every error is visible to the user and recoverable.

## Testing Strategy

### Layers

- **Unit tests** — pure functions in `data/`, `ai/`, `recap/`: signal computation, prompt rendering, recap assembly. pytest.
- **Integration tests** — mock yfinance HTTP via `respx`, mock Anthropic API; run full `generate_daily_recap` end-to-end and assert DB state.
- **Web tests** — `httpx.AsyncClient` against the FastAPI app; verify route behavior, HTMX fragment shape, auth enforcement.

### Not Tested in CI

- Real yfinance and Claude calls (slow, flaky, costly). Covered by a manual `scripts/smoke_test.py` run periodically.

### TDD Discipline

- Required for: business logic (signal evaluation, cache expiry, prompt versioning, recap composition).
- Not required for: template styling, layout tweaks.

## Configuration

All runtime config via environment variables (pydantic-settings):

```
APP_PASSWORD_HASH        bcrypt hash for single-user login
SESSION_SECRET           cookie signing key
ANTHROPIC_API_KEY        Claude API key
DATABASE_URL             default: sqlite:////data/marketpulse.db
WATCHLIST_RECAP_TIME     default: "16:30" (America/New_York)
LOG_LEVEL                default: INFO
AI_MODEL                 default: claude-sonnet-4-6
AI_CACHE_TTL_HOURS       default: 24
```

## Deployment

**Platform:** Fly.io (free tier sufficient for single user).

**Container:** multi-stage Dockerfile based on `python:3.12-slim`; uv installs locked deps; non-root user.

**fly.toml highlights:**
- 1 VM, `shared-cpu-1x`, 256MB
- 1GB persistent volume mounted at `/data`
- HTTP service exposing port 8000

**Startup:** `alembic upgrade head && uvicorn marketpulse.web.main:app --host 0.0.0.0 --port 8000`

**Health check:** `GET /health` returns 200 when DB reachable and APScheduler reports running.

**Logging:** structured JSON to stdout via `structlog`, aggregated by Fly.

**Backup:** weekly cron job dumps SQLite to a timestamped file in the volume; user manually pulls snapshots periodically. (Cloud-storage push is a v1.1 nice-to-have.)

### Observability

Intentionally minimal for v1:

- Structured logs with request ID, ticker, duration, external API status
- Recap job outcomes captured in `daily_recaps.generation_status` and `error_message`
- No Sentry/Prometheus — single user reads logs directly when something looks wrong

## Project Skeleton

```
MarketPulse/
├─ pyproject.toml          # uv-managed
├─ uv.lock
├─ Dockerfile
├─ fly.toml
├─ alembic.ini
├─ alembic/
│   └─ versions/
├─ marketpulse/
│   ├─ web/
│   ├─ data/
│   ├─ recap/
│   ├─ ai/
│   ├─ db/
│   ├─ scheduler/
│   ├─ auth/
│   └─ config.py
├─ tests/
│   ├─ unit/
│   ├─ integration/
│   └─ web/
├─ scripts/
│   └─ smoke_test.py
└─ docs/
    └─ superpowers/
        └─ specs/
            └─ 2026-05-10-marketpulse-design.md  # this document
```

## Open Questions for Implementation Plan

- Exact watchlist signal thresholds (5% / 2x / MA periods) — sensible defaults in v1, configurable later
- Backup destination beyond local volume (deferred)
- Whether to expose a JSON API alongside HTMX routes (deferred until a real consumer exists)
