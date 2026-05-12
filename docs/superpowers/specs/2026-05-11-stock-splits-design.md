# Stock Splits — Design Spec

**Status:** Approved, ready for implementation plan
**Author:** harvey
**Date:** 2026-05-11

## Goal

Treat stock splits as a first-class corporate-action event, separate from
buy/sell trades. The system detects splits automatically from yfinance,
preserves original trade data, and adjusts current holdings + display
without user intervention.

## Why now

Today we model splits by hacking a `Trade(action="buy", quantity=N, price=0,
notes="1:2 拆股调整")` row. This works mechanically but:

- Confuses trade history (a $0 buy shows up alongside real trades)
- Loses split-ratio metadata (only reverse-engineerable from notes)
- Requires the user to do something the broker does automatically
- Doesn't compose: multiple splits or reverse splits become hairy

Industry-standard trackers (Robinhood, IBKR, 雪球) model splits as their own
event type. We adopt the same.

## Data Model

New table `stock_splits`:

```python
class StockSplit(Base):
    id: int (primary key)
    ticker: str(16)
    ex_date: date              # Effective date of the split (ex-date)
    ratio: float               # new_shares / old_shares
                               # forward 1:2 = 2.0
                               # reverse 5:1 = 0.2
    source: str(16)            # "yfinance" | "manual" | "import"
    notes: str | None
    created_at: datetime

    __table_args__ = (
        UniqueConstraint("ticker", "ex_date", name="uq_splits_ticker_date"),
        CheckConstraint("ratio > 0 AND ratio != 1", name="ck_splits_ratio_valid"),
    )
```

The CHECK constraint is belt-and-suspenders alongside service-layer
validation — a ratio of 0 would divide-by-zero in `avg_cost / ratio`, and a
ratio of 1 is a no-op that shouldn't be in the table.

`ratio` is stored as a single float because real splits are always exact
integer ratios where float64 has more than enough precision (smallest real
ratio in market history: 1:10000 = 0.0001).

`source` lets us tell auto-detected from manual entries so reconciliation
can prefer one over the other.

## Components

### `marketpulse/holdings/splits.py` — service layer

```python
def record_split(session, *, ticker, ex_date, ratio, source="manual", notes=None) -> StockSplit
def get_splits_for_ticker(session, ticker) -> list[StockSplit]
def delete_split(session, split_id) -> str  # returns ticker, for recompute

# total_split_factor is intentionally NOT exposed. Earlier drafts considered
# pre-computing a cumulative multiplier, but recompute_ticker walks the event
# timeline directly, so a separate factor function would just duplicate logic.
```

### `marketpulse/holdings/trades.py::recompute_ticker` — updated

The existing function walks Trade rows in chronological order to rebuild
`Holding.quantity`, `Holding.avg_cost`, and `Trade.realized_pl` on every
sell. With splits we add a second timeline:

```python
# Normalize all event times to datetime so mixed date/datetime comparisons
# never raise. A split's ex_date is anchored to end-of-day, so a same-day
# trade (executed during market hours) sorts BEFORE the split takes effect.
EOD = time(23, 59, 59)
events = sorted(
    [(t.executed_at, 0, "trade", t) for t in trades] +
    [(datetime.combine(s.ex_date, EOD), 1, "split", s) for s in splits],
    key=lambda x: (x[0], x[1]),
)

qty = 0
avg_cost = 0
for when, _, kind, evt in events:
    if kind == "trade":
        # existing buy/sell logic
    elif kind == "split":
        # ratio adjusts both:
        qty = qty * evt.ratio
        avg_cost = avg_cost / evt.ratio if evt.ratio else avg_cost
```

Crucial: **Trade rows are never rewritten.** A 2022 buy of 70 shares at
$28.27 stays exactly that in the DB, even after a 2025 1:2 split. The
post-split equivalent (140 @ $14.135) is computed at compute-time.

`realized_pl` on historical sells is **not** retroactively adjusted —
that P&L was settled at the time in real dollars and stays as-is.

### `marketpulse/scheduler/jobs.py::detect_corporate_actions` — new daily job

```python
def detect_corporate_actions() -> None:
    """Pull split history from yfinance for every ticker we hold or watch.

    Idempotent — re-runs are safe due to the (ticker, ex_date) unique
    constraint. New splits trigger recompute_ticker for that ticker.
    """
    tickers = {h.ticker for h in db.query(Holding).all()} \
            | {w.ticker for w in db.query(WatchlistItem).all()}
    for t in tickers:
        try:
            splits = yfinance.Ticker(t).splits  # pandas Series of (date, ratio)
        except Exception as exc:
            log.warning("split_fetch_failed", ticker=t, error=str(exc))
            continue
        for ts, ratio in splits.items():
            ex_date = ts.date()
            try:
                record_split(db, ticker=t, ex_date=ex_date,
                             ratio=float(ratio), source="yfinance")
                recompute_ticker(db, t)
                log.info("split_recorded", ticker=t, ex_date=str(ex_date),
                         ratio=float(ratio))
            except IntegrityError:
                pass  # already recorded, no-op
```

Scheduled alongside the existing daily recap (16:30 ET, Mon-Fri). Adds a
~10s yfinance call per watched/held ticker, runs once per day.

### `marketpulse/web/routes/splits.py` — API

```
POST   /splits             — body: ticker, ex_date, ratio, notes
                             Validates ratio > 0 and ratio != 1 (HTTP 422 otherwise)
                             Returns: JSON of created row
                             Calls recompute_ticker after insert
GET    /splits             — Query: ?ticker=X (optional)
                             Returns: JSON list
DELETE /splits/{id}        — Removes row, recomputes that ticker
```

Manual entry path. Used as a fallback when yfinance is wrong or delayed.

## UI Changes

### `/trades` view — unified timeline

The page currently lists Trade rows only. We change the backend to union
Trade + StockSplit + Dividend, sort by event time, and the template
renders three different row shapes:

| Date | Ticker | Type | Detail | P&L | Action |
|---|---|---|---|---|---|
| 2025-07-30 | TQQQ | 🟧 卖出 | 8 股 @ $90.04 | +$X | 删除 |
| 2025-09-24 | TQQQ | 💰 分红 | $0.10/股 总 $1.96 | — | 删除 |
| **2025-11-20** | **TQQQ** | **🟪 拆股** | **1 → 2 (比例 2.0)** | — | **删除** |
| 2025-12-24 | TQQQ | 💰 分红 | $0.09/股 总 $3.42 | — | 删除 |

Top of page gets a filter strip: `[全部] [仅买卖] [仅拆股] [仅分红]`.
Default: 全部.

### `/trades` POST form

Add a "类型" dropdown to the existing form: 买入 / 卖出 / 拆股 / 分红.
Selecting "拆股" swaps the form fields:

- Original price + quantity → hidden
- New: 比例 (ratio, e.g. "2" or "0.2")

Form submits to `POST /splits` (or `/dividends`) routing based on selected type.

## Migration of Existing Hack Data

A one-off script `scripts/cleanup_split_hacks.py`:

```python
"""Convert any pre-feature 'price=0 buy with 拆股 in notes' Trade rows
into proper StockSplit entries, then delete the hack rows. Idempotent.
"""
hack_rows = session.query(Trade).filter(
    Trade.price == 0,
    Trade.notes.like("%拆股%"),
).all()

unparsed: list[int] = []  # trade IDs where notes didn't match — flagged for review

for t in hack_rows:
    # Parse ratio from notes (formats seen: "1:2", "1 → 2", "1拆2")
    m = re.search(r"(\d+)\s*[:→拆\-]\s*(\d+)", t.notes)
    if m:
        ratio = int(m.group(2)) / int(m.group(1))
    else:
        ratio = 2.0  # default 1:2
        unparsed.append(t.id)
        log.warning("split_migration_fallback", trade_id=t.id,
                    notes=t.notes, defaulted_ratio=ratio)

    try:
        record_split(session, ticker=t.ticker,
                     ex_date=t.executed_at.date(),
                     ratio=ratio, source="import",
                     notes=f"Migrated from trade #{t.id}: {t.notes}")
    except IntegrityError:
        pass  # already migrated

    session.delete(t)

session.commit()

for ticker in {t.ticker for t in hack_rows}:
    recompute_ticker(session, ticker)

if unparsed:
    print(f"⚠️  {len(unparsed)} hack rows used the default 2.0 ratio "
          f"because notes didn't parse: trade_ids={unparsed}")
    print("Review these manually and POST /splits with the correct ratio if wrong.")
```

Not run inside alembic — too much potential for ambiguity on rerun. Operator
runs this once after deploy, reviews any fallback warnings, then deletes the
script from the repo.

## Edge Cases

| Case | Behavior |
|---|---|
| yfinance returns no splits for a ticker | No-op, no error |
| yfinance is down / rate-limited | Log warning, skip ticker, retry tomorrow |
| Reverse split (ratio < 1) | Same formula; produces fractional shares which we keep as float |
| Split happens for ticker we don't hold yet | Row inserted into stock_splits; will apply if user later buys + then `recompute_ticker` is called |
| User manually records a split that yfinance later reports | Unique constraint prevents duplicate; manual entry stays (since auto-detect IGNOREs on conflict) |
| User deletes a split | `recompute_ticker(ticker)` runs without it; cumulative factor reduced |
| Multiple splits on same ticker | Compute applies each in chronological order; cumulative factor is the product |
| Split + trade on same date | `ex_date` (date) vs `executed_at` (datetime). Trade with `executed_at.date() < ex_date` is pre-split; same-day trades sort by datetime, splits sort after end-of-day. Documented in `recompute_ticker` |

## Testing

| File | Coverage |
|---|---|
| `tests/unit/test_splits.py` (new) | `record_split`, `total_split_factor`, ratio bounds (0 < ratio, ratio != 1), uniqueness |
| `tests/integration/test_trades.py` (extend) | `recompute_ticker` with: 1× forward split, 1× reverse split, 2× consecutive splits, split + delete = restore, fractional-share precision after reverse split |
| `tests/web/test_splits.py` (new) | POST /splits creates row + triggers recompute; GET filters; DELETE recomputes |
| `tests/web/test_trades.py` (extend) | /trades timeline shows split events with correct chip; filter strip works |
| `tests/unit/test_scheduler_jobs.py` (extend) | `detect_corporate_actions`: idempotent re-runs, yfinance failure does not propagate |

## File Manifest

**New:**

- `marketpulse/holdings/splits.py`
- `marketpulse/web/routes/splits.py`
- `alembic/versions/0007_stock_splits.py`
- `tests/unit/test_splits.py`
- `tests/web/test_splits.py`
- `scripts/cleanup_split_hacks.py` (one-off, removed after run)

**Modified:**

- `marketpulse/db/models.py` — add StockSplit
- `marketpulse/holdings/trades.py::recompute_ticker` — splits-aware walk
- `marketpulse/scheduler/jobs.py` — add `detect_corporate_actions` job + schedule
- `marketpulse/web/routes/trades.py` — union timeline (Trade + Split + Dividend)
- `marketpulse/web/templates/trades.html` — type filter strip + form dropdown
- `marketpulse/web/templates/partials/trades_table.html` — three row shapes
- `marketpulse/web/main.py` — register splits router

## Future Optimizations

Not needed at current scale (single user, <100 tickers, <1000 trades per
ticker), but documented so future-us doesn't re-derive:

- **recompute_ticker caching:** For tickers with many splits + thousands of
  trades, cache a per-ticker `(splits_hash, cumulative_factor)` tuple in
  Holding so we can skip the full walk when nothing relevant changed.
- **yfinance concurrency:** `detect_corporate_actions` is currently serial
  (~10s/ticker). If the universe grows past ~50 tickers, batch with a
  thread pool (yfinance is I/O-bound) and add exponential-backoff retries
  for transient HTTP failures.
- **Broker-reconciled fractional handling:** Real brokers cash out
  fractional shares after reverse splits. We keep floats; if the user
  ever syncs with a real brokerage feed, we'll need a reconciliation pass.

## Out of Scope

Deferred to later iterations:

- Spinoffs / mergers (different math from splits)
- Reverse split that produces fractional shares the broker rounds to cash
  (we keep float quantity; user can manually correct)
- Stock dividends (shares received as a dividend instead of cash) — handled
  as a separate event type, not a split
- Per-broker split confirmation flow (some brokers round differently)
- Bulk-edit of historical splits in the UI
