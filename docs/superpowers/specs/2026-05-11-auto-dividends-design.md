# Auto-Detect Dividends + Consolidate to Tencent — Design Spec

**Status:** Approved, ready for implementation plan
**Author:** harvey
**Date:** 2026-05-11
**Builds on:** `2026-05-11-stock-splits-design.md` (PR #1, merged)

## Goal

1. Auto-detect cash dividends from the Tencent `fqkline` endpoint and persist
   them with correctly-computed `total_amount` (= per-share × shares-held on
   ex_date).
2. Consolidate the existing `detect_corporate_actions` daily job onto Tencent
   as the primary source for **both** splits and dividends, with yfinance as
   fallback. This replaces the yfinance-only split path shipped in PR #1.
3. Add a `quantity_as_of(session, ticker, as_of_date)` helper that derives
   historical share counts from the Trade + StockSplit timeline — needed by
   the dividend total computation, and useful on its own.

## Why now

The stock-splits feature (PR #1) added a daily scheduler job that auto-detects
splits from yfinance. Dividends are still manual: the user POSTed 14 TQQQ
dividends one-by-one from 腾讯自选股 screenshots. While investigating Tencent's
API for an alternative source, we discovered that the `fqkline` endpoint
already returns **both** dividends and splits embedded in each day's K-line
array — one call gives us everything:

```
['2024-06-10', open, close, high, low, vol, {
  'FHcontent': '',                        # dividend description ("每股分配 X 美元")
  'hgcgContent': '每1股拆分成10股',         # split description
  'cqr': '2024-06-10'                     # ex_date
}]
```

Switching to Tencent as primary source:
- Eliminates manual dividend entry (the original pain point)
- Removes the Mihomo proxy dependency for corporate-action detection (Tencent
  works directly from mainland China; yfinance does not)
- Reduces yfinance API load (was: 1 splits call per ticker per day; becomes:
  fallback only)
- Keeps yfinance available for the cases Tencent doesn't cover (rare tickers
  on `.OQ`/`.N` suffixes, or transient Tencent outages)

## Data Model Changes

### `Dividend` model gains 3 things

```python
class Dividend(Base):
    # ... existing columns ...
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual",
        # values: "manual" | "tencent" | "yfinance" | "import"
    )

    __table_args__ = (
        Index("ix_dividends_ticker_ex_date", "ticker", "ex_date"),
        UniqueConstraint("ticker", "ex_date", name="uq_dividends_ticker_date"),
        CheckConstraint(
            "amount_per_share >= 0 AND total_amount >= 0",
            name="ck_dividends_amounts_non_negative",
        ),
    )
```

Mirrors the `StockSplit` model exactly. The `UniqueConstraint` is the key
addition: today the table has only a non-unique index, so a daily-job re-run
would insert duplicate rows. After Alembic 0008 lands, duplicates are
DB-rejected and the scheduler swallows `DividendError("already recorded")`.

### Alembic migration 0008

```python
"""add dividends source + unique constraint + check

Revision ID: 0008
Revises: 0007
"""

def upgrade():
    op.add_column("dividends",
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"))

    # Defensive dedup — older versions allowed duplicate (ticker, ex_date).
    op.execute("""
        DELETE FROM dividends WHERE id NOT IN (
            SELECT MIN(id) FROM dividends GROUP BY ticker, ex_date
        )
    """)

    with op.batch_alter_table("dividends") as batch:
        batch.create_unique_constraint("uq_dividends_ticker_date", ["ticker", "ex_date"])
        batch.create_check_constraint(
            "ck_dividends_amounts_non_negative",
            "amount_per_share >= 0 AND total_amount >= 0",
        )


def downgrade():
    with op.batch_alter_table("dividends") as batch:
        batch.drop_constraint("ck_dividends_amounts_non_negative", type_="check")
        batch.drop_constraint("uq_dividends_ticker_date", type_="unique")
    op.drop_column("dividends", "source")
```

`batch_alter_table` is required for SQLite ALTER TABLE ADD CONSTRAINT — Alembic
implements it by recreating the table.

## Components

### `marketpulse/data/tencent_client.py::fetch_corporate_actions` — new method

```python
@dataclass
class CorporateActions:
    dividends: list[tuple[date, float]]  # (ex_date, amount_per_share)
    splits: list[tuple[date, float]]     # (ex_date, ratio)


def fetch_corporate_actions(
    self, ticker: str, *, start: date, end: date,
) -> CorporateActions:
    """Pull corporate actions from Tencent's fqkline endpoint.

    URL: https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
         ?param=us{TICKER}.OQ,day,{start},{end},1825,qfq

    Tries the same `_SUFFIXES` ("", ".OQ", ".N") as fetch_quote.

    The `qfq` mode returns each day's OHLCV plus an optional dict at index 6:
        {"FHcontent": "每股分配 X 美元", "hgcgContent": "...", "cqr": "..."}

    Parses:
      - FHcontent  ~ r"每股分配([\\d.]+)美元"           → dividend per-share
      - hgcgContent ~ r"每(\\d+)股拆分成(\\d+)股"        → forward split ratio = b/a
      - hgcgContent ~ r"每(\\d+)股合并成(\\d+)股"        → reverse split ratio = b/a
      - cqr        → ex_date (YYYY-MM-DD)

    Unparseable entries: log.warning + skip (do not propagate).
    Empty response / unknown ticker: return CorporateActions([], []).

    A single day can contribute to BOTH lists (e.g. same-date split + dividend)
    if both FHcontent and hgcgContent are populated. Each is parsed independently.
    """
```

### `marketpulse/data/yfinance_client.py::fetch_dividends` — new method

```python
@_retry
def fetch_dividends(self, ticker: str) -> list[tuple[date, float]]:
    """Mirror of fetch_splits but for cash dividends.

    Returns (ex_date, amount_per_share) pairs from yf.Ticker(t).dividends.
    Empty list if yfinance has none. Used as fallback when Tencent fails.
    """
```

### `marketpulse/holdings/quantity_history.py` — new module

```python
def quantity_as_of(session: Session, ticker: str, as_of: date) -> float:
    """Share quantity held at end of `as_of`, derived from all Trade and
    StockSplit events with timestamps ≤ as_of (EOD-anchored). Returns 0 if
    the ticker was never held or fully sold before as_of.

    Internally uses the same _walk_events helper as recompute_ticker, with
    early termination when an event's `when` exceeds as_of.
    """
```

Shared helper extracted from `recompute_ticker`:

```python
# In marketpulse/holdings/trades.py
def _walk_events(
    session: Session, ticker: str, *, until: date | None = None,
) -> tuple[float, float, list[Trade]]:
    """Walk Trade + StockSplit events for `ticker` in chronological order.

    Returns final (quantity, avg_cost, processed_trades). The trades list
    is needed by recompute_ticker to set realized_pl; quantity_as_of ignores it.

    If `until` is provided, stops processing after the last event with
    when.date() <= until.
    """
```

`recompute_ticker` becomes a thin wrapper calling `_walk_events(until=None)`
and persisting the result to `Holding`. `quantity_as_of` calls
`_walk_events(until=as_of)` and returns just the qty.

### `marketpulse/holdings/dividends.py` changes

```python
def record_dividend(
    session, *, ticker, ex_date, amount_per_share, total_amount,
    source: str = "manual",
    notes: str | None = None,
) -> Dividend:
    """Persist a dividend. Raises DividendError on invalid input or duplicate
    (ticker, ex_date) — IntegrityError is caught and re-raised as
    DividendError('already recorded for X on Y') so the scheduler can swallow it.
    """

def delete_dividend(session, dividend_id) -> str:
    """Delete by id. Returns the affected ticker. Raises DividendError if missing.
    Added for symmetry with delete_split; not yet used by the route layer."""
```

### `marketpulse/scheduler/jobs.py::run_detect_corporate_actions` — rewritten

```python
def run_detect_corporate_actions() -> None:
    """Daily 17:00 ET: pull splits + dividends from Tencent for every
    held/watched ticker; on Tencent failure fall back to yfinance.

    Idempotent — duplicates are swallowed at the service layer.
    """
    log.info("detect_corporate_actions_start")
    tencent = TencentClient()
    yf = YFinanceClient()
    today = date.today()
    since = today - timedelta(days=1825)  # 5 years lookback

    gen = session_scope()
    db = next(gen)
    try:
        tickers = unique_tickers(db)  # held ∪ watched, insertion-ordered
        for t in tickers:
            actions, src = _fetch_actions(t, tencent, yf, since, today)
            if actions is None:
                continue  # both sources failed; logged

            recompute_needed = False

            # Splits: record for all tickers (incl. watchlist-only).
            for ex_date, ratio in actions.splits:
                try:
                    record_split(db, ticker=t, ex_date=ex_date, ratio=ratio, source=src)
                    log.info("split_recorded", ticker=t, ex_date=str(ex_date), ratio=ratio, source=src)
                    recompute_needed = True
                except SplitError:
                    pass  # already recorded

            # Dividends: only record when shares held on ex_date.
            for ex_date, per_share in actions.dividends:
                qty = quantity_as_of(db, t, ex_date)
                if qty <= 0:
                    continue
                try:
                    record_dividend(
                        db, ticker=t, ex_date=ex_date,
                        amount_per_share=per_share,
                        total_amount=qty * per_share,
                        source=src,
                    )
                    log.info("dividend_recorded", ticker=t, ex_date=str(ex_date),
                             per_share=per_share, qty=qty, source=src)
                except DividendError:
                    pass  # already recorded

            if recompute_needed:
                recompute_ticker(db, t)
    finally:
        db.close()
    log.info("detect_corporate_actions_done")


def _fetch_actions(ticker, tencent, yf, since, today):
    """Tencent first; yfinance fallback. Returns (CorporateActions, source_label)
    or (None, _) on total failure. Never raises."""
    try:
        return tencent.fetch_corporate_actions(ticker, start=since, end=today), "tencent"
    except Exception as exc:  # noqa: BLE001
        log.warning("tencent_corp_actions_failed", ticker=ticker, error=str(exc))
    try:
        splits = yf.fetch_splits(ticker)
        dividends = yf.fetch_dividends(ticker)
        return CorporateActions(dividends=dividends, splits=splits), "yfinance"
    except Exception as exc:  # noqa: BLE001
        log.warning("corp_actions_all_sources_failed", ticker=ticker, error=str(exc))
        return None, "none"
```

Cron schedule unchanged: `CronTrigger(hour=17, minute=0, day_of_week="mon-fri")`.

## Edge Cases

| Case | Behavior |
|---|---|
| Tencent returns no actions (no splits, no dividends ever) | Empty `CorporateActions`, no-op |
| Tencent endpoint returns `{"code": 11, ...}` (bad route) | Treat as exception → yfinance fallback |
| FHcontent format we don't recognize (e.g. "特别分红 0.05 美元") | log.warning, skip that one entry, continue |
| Dividend ex_date when user held 0 shares (watchlist-only, or sold before) | Skip — don't record |
| Dividend ex_date matches a same-day buy | `quantity_as_of` uses EOD-anchored timeline — same-day buy counts, so dividend is recorded |
| User adds a new ticker to watchlist today | Next daily run pulls 5 years of history, records all retroactively (idempotent) |
| User manually entered a dividend that Tencent later returns | UNIQUE constraint blocks the auto-insert; manual entry survives (no overwrite) |
| Tencent returns dividend but yfinance disagrees on amount | Manual reconciliation; out of scope |
| Reverse split (ratio < 1) | Parsed via `每X股合并成Y股` regex; ratio = Y/X < 1 |
| Same date has BOTH dividend AND split | Two separate parses (FHcontent + hgcgContent); recorded as two events |
| Tencent endpoint changes payload schema | Parser fails individual entries with log.warning, doesn't propagate |
| `quantity_as_of` for ticker with no Trade rows yet | Returns 0 |

## Testing

| File | Coverage |
|---|---|
| `tests/unit/test_quantity_history.py` (new) | qty=0 when never held; correct qty after multiple buys/sells; correct qty after split; correct qty after split+sell |
| `tests/unit/test_tencent_corporate_actions.py` (new) | parse FHcontent (multiple formats); parse hgcgContent (forward + reverse); empty fqkline response; HTTP error → exception; bad-JSON → exception; unparseable strings → logged and skipped |
| `tests/unit/test_yfinance_dividends.py` (new) | mock pandas Series → list of tuples; empty Series → `[]` |
| `tests/unit/test_dividends.py` (extend) | `source` parameter persisted; `IntegrityError` → `DividendError("already recorded")`; `delete_dividend` returns ticker / raises on missing |
| `tests/unit/test_scheduler_jobs.py` (extend) | Tencent ok records both event types; Tencent fails → yfinance fallback; both fail → no raise; qty=0 dividend skipped; duplicate dividend swallowed; parsing failure isolated |
| `tests/integration/test_trades.py` (extend) | `recompute_ticker` still passes after refactor to `_walk_events` (regression test) |

## File Manifest

**New:**
- `marketpulse/holdings/quantity_history.py`
- `alembic/versions/0008_dividends_source_and_unique.py`
- `tests/unit/test_quantity_history.py`
- `tests/unit/test_tencent_corporate_actions.py`
- `tests/unit/test_yfinance_dividends.py`

**Modified:**
- `marketpulse/db/models.py` — `Dividend` adds source + unique + check
- `marketpulse/holdings/dividends.py` — `record_dividend` source param + duplicate handling; new `delete_dividend`
- `marketpulse/holdings/trades.py` — extract `_walk_events` shared helper
- `marketpulse/data/tencent_client.py` — `fetch_corporate_actions` method + `CorporateActions` dataclass
- `marketpulse/data/yfinance_client.py` — `fetch_dividends` method
- `marketpulse/scheduler/jobs.py` — rewrite `run_detect_corporate_actions` with Tencent primary + yfinance fallback + dividend recording
- `tests/unit/test_dividends.py` — extend for source + dedup + delete
- `tests/unit/test_scheduler_jobs.py` — 6 new scenarios

## Deployment Notes

After this PR merges and deploys:

1. Apply Alembic migration 0008. Existing 14 manual TQQQ dividend rows get
   `source="manual"` via `server_default`.
2. **Wipe the manual dividend rows** so the new auto-detection path can
   repopulate them from a single trusted source with `quantity_as_of`-computed
   totals:
   ```sql
   DELETE FROM dividends WHERE source = 'manual';
   ```
3. Trigger the scheduler job manually (or wait for the next 17:00 ET run):
   ```bash
   python -c "from marketpulse.scheduler.jobs import run_detect_corporate_actions; \
              run_detect_corporate_actions()"
   ```
4. Verify on `/trades` that the 14+ TQQQ dividends reappear from Tencent. Spot-check
   3-4 ex_dates against the original screenshots — `amount_per_share` should
   match exactly; `total_amount` may differ slightly if the old import used
   a different share-count snapshot.

This is one-time operator work; not codified as a migration script.

## Out of Scope

- Backfill script for tickers with very old splits not yet in the DB
  (daily job's 5-year lookback handles tickers added today; for tickers with
  splits older than 5 years, manual entry via POST /splits is required)
- UI badge showing dividend source ("tencent" / "yfinance" / "manual") — can be
  added later as a column or chip on the `/trades` timeline
- Reconciliation tool when Tencent and yfinance disagree on a dividend amount
- `POST/GET/DELETE /dividends/{id}` route changes — service layer adds
  `delete_dividend` and `source` param for completeness, but no new HTTP surface
  in this PR
- Special dividend / stock dividend / spinoff parsing (Tencent's `FHcontent`
  format for these is unverified; parser will log + skip, which is the right
  default until we have real examples to test against)
