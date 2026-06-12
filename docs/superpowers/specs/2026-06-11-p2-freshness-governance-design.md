# P2 Freshness Governance — final vs provisional bars (PR1)

**Date:** 2026-06-11
**Status:** Approved (design locked)
**Charter link:** data-trust chain (CHARTER § research-trustworthiness evidence chain); feeds the
P2 price-freshness data-trust precondition. **Priority: P2 blocker** — statistical validation
(strategy-trust chain) builds on price correctness, so this ships first.

## Problem (measured, not theoretical)

`price_cache` has no concept of bar finality. The 2026-06-11 audit found:

- **24 provisional rows in prod** — bars fetched intraday (e.g. 10:24 ET, 12:30 ET) and stored
  as the day's bar.
- The pinning mechanism is `DataService.get_history`'s freshness check
  (`(end - cached[-1].date).days <= 1` → serve cache, no refetch): an intraday bar written today
  is not refreshed today **or tomorrow**; it self-heals only ≥2 days later when a 60d refetch
  overwrites it. (`PriceCache.upsert` itself is a true UPSERT — overwrite is possible, it just
  isn't triggered in time.)
- `snapshot_runner._read_price_lookup` reads `price_cache` directly
  (`max(date) <= trading_date`, no finality filter) at the 21:30 UTC NAV tick — **before** any
  self-heal can happen.
- **Confirmed contamination:** SPY 2026-06-10 pinned at a midday price (730.72, fetched
  12:30 ET) flowed into the 2026-06-10 **and** 2026-06-11 `paper_nav_snapshot` rows
  (06-11 fell back to the contaminated 06-10 bar under the `<= trading_date` convention).
  **2 of 9 north-star days are contaminated.** price_cache later self-healing cannot fix the
  already-written snapshots.

## Goal

The north-star pipeline becomes structurally unable to consume an intraday price. Contaminated
snapshots are rebuilt. All code changes are deterministic; no new dependencies.

## Scope (locked — two-PR split)

**PR1 (this spec): stop the bleeding.**
- Alembic migration: `is_final` / `finalized_at` on `price_cache` + Python backfill of 7,352 rows.
- Write-time finality in `PriceCache.upsert`.
- Post-close **FinalizeJob** refreshing provisional bars, mounted as step 0 of the NAV tick
  (+ standalone CLI).
- NAV read path (`_read_price_lookup`) consumes `is_final = true` only.
- Rebuild the two contaminated snapshots (2026-06-10, 2026-06-11) with
  `is_rebuilt = true`, `rebuild_reason = 'provisional_price_cache_fix'`.
- CHARTER note recording the PR2 deferral.

**PR2 (deferred — recorded in CHARTER as future hardening, NOT in this PR):**
evaluation / backtest read paths consume only-final bars. Rationale: the NAV contamination is a
*proven production* defect; evaluation/backtest exposure is a *plausible-but-unproven research*
defect, and once the FinalizeJob exists their nightly/historical reads are de-risked anyway.
Also out of scope: half-day-aware calendars, upstream-correction eventing, walk-forward tooling.

**Consumption rule (end-state, locked):** NAV / North Star / Backtest read final bars only —
PR1 implements the NAV leg; PR2 extends to backtest/evaluation.
**Exception rule (locked):** today-facing dashboard surfaces MAY read provisional data but must
present it explicitly as live/provisional, never as a settled close. Current surfaces use
`QUOTE_CACHE` live quotes (already presented as live); implementation verifies no surface renders
a provisional close as final — if one does, it gets a provisional marker. No new UI work expected.

## Design

### 1. Schema (Alembic + Python backfill)

```sql
price_cache
-----------
is_final     BOOLEAN  NOT NULL DEFAULT 0
finalized_at TIMESTAMP NULL
-- fetched_at retained unchanged (the "source_fetched_at" in the design discussion)
```

Backfill runs in Python inside the migration (not raw SQL — the close cutoff is an
America/New_York wall-clock rule and UTC offsets shift with DST): for each of the 7,352 rows,
`is_final = fetched_at(NY) >= bar_date 16:05 ET`; when true, `finalized_at = fetched_at`.
The 24 audited provisional rows land `false`. No new index (reads remain PK-driven).

### 2. Finality rule (locked)

A bar for `bar_date` is final iff it was fetched at or after **16:05 America/New_York on
`bar_date`** (tz-aware comparison; constant `FINAL_CUTOFF_NY = time(16, 5)`).
Half-days: `NYTradingCalendar` has no early-close knowledge; on a 13:00 ET half-day a
13:05-16:04 fetch is treated provisional for a few extra hours — conservative and harmless.
Downgrade is impossible: a past date's cutoff is always before "now", so re-fetching a final bar
keeps it final.

### 3. Write-time finality (`PriceCache.upsert`)

`upsert` computes `is_final` per bar at write time using the rule above, and sets
`finalized_at = fetched_at` (this write's fetch timestamp) when final — **`finalized_at` means
"when the final bar was obtained from the source", never "when a job processed it"**; job
runtime belongs in logs, not in price rows. Bars fetched after close are final immediately —
the normal nightly writes never need a second pass. The ON CONFLICT update overwrites
OHLCV/fetched_at/is_final/finalized_at together, so a provisional row refreshed after close
flips to final atomically.

### 4. FinalizeJob — `marketpulse/data/finalize.py`

`finalize_provisional_bars(session, *, lookback_trading_days=5) -> FinalizeResult`:

The lookback is **NY trading days**, not calendar days: the cutoff date is computed by walking
back 5 sessions with the existing `NYTradingCalendar` (weekends/holidays excluded).

1. Select distinct tickers having `is_final = false` rows with `date >= cutoff`,
   **always unioned with `{"SPY"}`** (benchmark leg always gets a refresh attempt).
2. For each selected ticker, fetch
   `YFinanceClient.fetch_history_range(ticker, start, end=today_ny)` where
   **`start = min(ALL of that ticker's provisional dates — not only those inside the window —,
   cutoff)`**. This closes the SPY hole: even when SPY's provisional row is older than the
   window, the forced union plus the all-provisional-dates `min()` reaches back to it (e.g. the
   known 06-10 contamination), instead of silently re-fetching only the recent 5 sessions.
3. Per-ticker failure: the bar stays provisional and the NAV read filter (below) falls back to
   the previous final close. Never raises out of the job. **Severity is tiered:** ordinary
   ticker failure → WARNING; **SPY failure → ERROR-level structured log** (`finalize_spy_failed`)
   — SPY is the north-star benchmark leg, its degradation must be loud while the tick still
   proceeds.
4. Returns counts (`tickers_attempted / bars_finalized / failures`) for structured logging.

**Mounting (locked choice):** step 0 of the NAV tick, immediately before
`_run_nav_snapshot_safely` — ordering is structural, not clock-based (a parallel cron ordered
only by wall clock is the same failure shape this bug came from). Also exposed as a standalone
CLI (`python -m marketpulse.jobs.finalize_prices`) for ops/backfill use.
Network use is allowed here: the tick is already a network-permitted path (P6b PriceProvider);
the zero-network guard applies to web/presenter paths, which this does not touch.

### 5. NAV read path

`snapshot_runner._read_price_lookup` adds `PriceCacheEntry.is_final == True` to **both** the
`max(date)` subquery **and** the join — join-only filtering is a known wrong shape (the subquery
selects a provisional `max(date)`, the join then excludes it, and a ticker that HAS a final
yesterday-close resolves to `None`). Semantics: a ticker whose latest bar is provisional values
at its previous final close — the existing, charter-tolerated `<= trading_date` / ~1-day-lag
convention. `unpriced_positions_count` / `unpriced_tickers` semantics unchanged.

**Fallback observability:** the snapshot run emits `provisional_fallback_count` and
`provisional_fallback_tickers` (tickers whose raw `max(date) <= trading_date` bar exists but was
excluded as provisional, so an older final close was used) in the structured NAV log. No schema
or UI change in PR1 — log fields only; otherwise post-fix we would be blind to how often NAV is
running on day-old closes.

### 6. Snapshot rebuild (one-off, after FinalizeJob has healed the data)

**Ordering is fixed, not implementation-discretionary** (06-11's north-star depends on the
healed prior state):

1. Run FinalizeJob (heals SPY 2026-06-10 and any other affected bars).
2. Rebuild 2026-06-10 (`run_nav_snapshot`, already idempotent).
3. Rebuild 2026-06-11.

Each rebuilt row gets `is_rebuilt = true`, `rebuild_reason = 'provisional_price_cache_fix'`.
The anchor day (2026-05-29) is not rebuilt, so the north-star baseline is untouched. Executed
via a small CLI/script during deploy, not a recurring job. Note: SPY 06-10 is *still* 730.72 in
prod today — the old self-heal path will not fix it before 06-12; the FinalizeJob does it
deterministically.

### 7. Documentation

CHARTER, data-trust chain entry, append: *Future hardening (PR2): evaluation/backtest read
paths consume only-final bars.*

## Error handling

- FinalizeJob: per-ticker isolation; failures leave bars provisional; NAV degrades to last final
  close (existing convention). Job never aborts the tick.
- Migration backfill: pure computation over existing rows; no network.
- Rebuild: `run_nav_snapshot` is idempotent; re-running on a healthy date is a no-op shape.

## Testing (TDD, `# Layer:` tags; run via `uv run pytest`, lint `uv run ruff check`)

1. **Finality rule unit tests:** EDT and EST dates either side of 16:05 ET; exactly-16:05;
   intraday → provisional; after-close → final.
2. **Migration/backfill:** seed rows (intraday fetch, after-close fetch, EST winter date) →
   correct `is_final`/`finalized_at` after upgrade.
3. **upsert write-time:** intraday bar → `is_final=false`; after-close bar → `true` +
   `finalized_at` set; provisional row re-upserted after close → flips final.
4. **FinalizeJob:** provisional rows + mocked fetch → flipped final, counts correct; fetch
   failure → row stays provisional, job returns failure count, no raise; SPY always attempted;
   **SPY older-than-window:** SPY provisional row dated before the lookback cutoff → fetch range
   still reaches back to it; **severity tiering:** ordinary ticker failure logs WARNING, SPY
   failure logs ERROR (`finalize_spy_failed`) and the run still completes.
5. **NAV filter:** today-provisional + yesterday-final → lookup returns yesterday's close;
   both final → today's; all-provisional ticker → None (unpriced path).
   **Wrong-shape regression (mandatory):** seed `date=06-11 is_final=false` +
   `date=06-10 is_final=true`, call with `trading_date=06-11` → MUST return the 06-10 close,
   not None (catches the join-only-filter bug where the subquery picks the provisional max).
   Fallback observability: this case also asserts `provisional_fallback_count == 1` and the
   ticker appears in `provisional_fallback_tickers`.
6. **Rebuild:** contaminated snapshot re-run after heal → `spy_close` changes,
   `is_rebuilt=true`, `rebuild_reason='provisional_price_cache_fix'`; anchor row untouched.
7. **Regression:** existing NAV/snapshot suites stay green; zero-network guards unaffected.

## Files touched

(As-built paths — `marketpulse/cli/` per repo CLI convention, superseding the draft's
`marketpulse/jobs/`; the tick mount landed in `paper_trading_tick.py`, the tick's actual body,
not `scheduler/jobs.py`.)

- `alembic/versions/f43156b7577f_price_cache_is_final.py` — create
- `marketpulse/db/models.py` — `PriceCacheEntry` + 2 columns
- `marketpulse/data/finality.py` — create (shared finality rule)
- `marketpulse/data/cache.py` — write-time finality in `upsert`
- `marketpulse/data/finalize.py` — create (FinalizeJob)
- `marketpulse/cli/finalize_prices.py` — create (CLI)
- `marketpulse/scheduler/paper_trading_tick.py` — finalize as NAV-tick step 0
- `marketpulse/portfolio/snapshot_runner.py` — `is_final` filter in `_read_price_lookup`
- `marketpulse/cli/rebuild_nav_snapshots.py` — one-off rebuild for 06-10/06-11
- `docs/CHARTER.md` — PR2 future-hardening note + Data Freshness & Provenance Layer naming
- tests per section above

No new dependencies. No new routes. One Alembic migration.
