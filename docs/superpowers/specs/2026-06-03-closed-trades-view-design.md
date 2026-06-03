# Closed Trades View — Design

**Date:** 2026-06-03
**Status:** Approved (design locked)

## Problem

Closed paper-trade positions (with exit price + realized P&L) exist in the DB
but are **not surfaced anywhere in the UI**:

- `/lab/paper-trading` → **Positions** section is `status='OPEN'` only.
- `/lab/paper-trading` → **Orders & Fills** is scoped to the *current operational
  window* (`PaperAuditEvent.timestamp >= window.started_at`) and is empty once a
  tick's order lifecycle completes.

So a user cannot answer "what trades have closed and how much did they make/lose?"
This is an observability gap (data exists; no view), not a speculative feature.

## Goal

Add a **Closed Trades** section to the bottom of `/lab/paper-trading` showing all
closed positions with entry/exit/realized P&L, plus a small summary. One page
answers: what do I hold (Positions), what did the system just do (Orders & Fills),
and what have I realized (Closed Trades).

## Scope (locked)

**In scope:**
- New bottom section "Closed Trades" on the existing `/lab/paper-trading` page.
- Summary cards: Realized P&L, Closed Trades count, Win Rate, Avg Return.
- Table (latest 50, exit-date DESC): Exit Date · Ticker · Strategy · Qty ·
  Entry · Exit · Days Held · P&L ($) · Return (%).
- Cache-only / zero-network presenter, mirroring the existing
  `SectionResult` + frozen-dataclass pattern.

**Out of scope (Phase 2, only when N grows to hundreds+):**
- Dedicated `/lab/trades-history` page, strategy/date filters, CSV export,
  pagination, hold-time analytics. No "Show All →" link now (would be a dead
  link); a muted count line is shown instead.

## Architecture

All in `marketpulse/trading/query_models.py` (the existing paper-trading
presenter) + the `lab_paper_trading.html` template. No new route (the existing
`/lab/paper-trading` GET already calls `load_paper_trading_dashboard`). DB-only —
satisfies the zero-network architecture guard.

### Data model (new frozen dataclasses)

```python
@dataclass(frozen=True)
class ClosedTradeRow:
    exit_date: date            # closed_at.date()
    ticker: str
    strategy: str
    quantity: int
    entry_price: Decimal
    exit_price: Decimal | None     # closed positions normally have one
    days_held: int                 # (exit_date - entry_date).days, calendar days
    realized_pnl: Decimal | None
    return_pct: float | None       # realized_pnl / (entry_price*quantity); see rules

@dataclass(frozen=True)
class ClosedTradesSummary:
    total_count: int               # ALL closed (not just the 50 shown)
    realized_pnl_total: Decimal    # Σ realized_pnl over all closed
    win_rate: float | None         # wins / total_count (None if total_count == 0)
    avg_return_pct: float | None   # mean of per-trade return_pct, skipping None

@dataclass(frozen=True)
class ClosedTrades:
    summary: ClosedTradesSummary
    rows: list[ClosedTradeRow]     # latest 50, exit-date DESC
    count_label: str               # "Showing latest 50 of N closed trades"
                                   # or "Showing N closed trades" when N <= 50
```

### Presenter

```python
_CLOSED_TRADES_LIMIT = 50

def _load_closed_trades_section(db: Session) -> SectionResult[ClosedTrades]:
    """All closed paper positions, newest exit first. Summary computed over the
    full closed set; table capped at _CLOSED_TRADES_LIMIT. DB-only."""
```

- Query closed rows ordered `closed_at DESC` (NULLS handled — closed positions
  have `closed_at`; defensively order by `closed_at desc, id desc`).
- Summary is computed over the **full** closed set (a second lightweight query or
  by fetching all closed rows once — see "Note on N" below), so it reflects
  all-time totals even though the table shows 50.
- Returns `section_ok(closed_trades, "No closed trades yet")` (empty rows →
  empty-state message, same pattern as `_load_positions_section`).

### Calculation rules (locked)

- **return_pct** = `float(realized_pnl) / float(entry_price * quantity)`.
  - **If `entry_price * quantity <= 0` or `entry_price`/`realized_pnl` is None →
    `return_pct = None`.** Such trades are **skipped** in the Avg Return mean.
- **days_held** = `(exit_date - entry_date).days` (calendar days). If either date
  is missing, `days_held = 0` (defensive; closed positions have both).
- **win_rate** = `count(realized_pnl > 0) / total_count`; `None` when
  `total_count == 0`.
- **avg_return_pct** = mean of the non-None `return_pct` values; `None` when there
  are no valid values.
- **realized_pnl_total** = `Σ realized_pnl` (treat None as 0 for the sum).

### Note on N (table cap vs full-set summary)

For the current scale (single-digit to low-hundreds of closed trades) the simplest
correct implementation is: fetch **all** closed positions once (ordered DESC),
compute the summary from the full list, then slice `[:50]` for the table rows.
This avoids a second query and keeps the summary all-time-accurate. The 50 cap is
purely a render limit. (If N ever reaches thousands, Phase 2's dedicated page adds
proper pagination — out of scope here.)

### Wiring

- Add `closed_trades: SectionResult[ClosedTrades]` to the `PaperTradingDashboard`
  frozen dataclass.
- In `load_paper_trading_dashboard`, build it via
  `_safe_section("Unable to load Closed Trades", lambda: _load_closed_trades_section(db))`
  and pass it through.
- Add the same field to `_shared_fetch_error_dashboard` as
  `section_error("Unable to load Closed Trades", degraded_reason)` so the
  degraded-dashboard path still constructs a valid object.

### Template

Add a "Closed Trades" block at the **bottom** of `lab_paper_trading.html`
(after Orders & Fills / Audit Timeline), following the existing section markup
(`mp-card`, section title, `mp-table`). Contents:

1. **Summary cards** (4): Realized P&L (color up/down), Closed Trades, Win Rate,
   Avg Return. Reuse the existing KPI-card styling used by the top strip.
2. **Count line** (muted): `{{ closed_trades.data.count_label }}`.
3. **Table**: Exit Date · Ticker (link to `/stock/{ticker}`) · Strategy · Qty ·
   Entry · Exit · Days Held · P&L ($, color) · Return (% , color). `None` numeric
   fields render as `—`.
4. **Empty state**: when `rows` is empty, show
   `closed_trades.empty_message` ("No closed trades yet"), consistent with other
   sections. Honor `SectionResult` error status (render the error message if the
   section degraded), matching how the template renders Positions/Orders.

## Error handling

The section is wrapped in `_safe_section`, so a query failure degrades to a
`section_error` and the rest of the page renders normally — identical to every
other dashboard section.

## Testing

**Presenter tests** (`tests/trading/test_query_models*.py` or the existing paper
dashboard test module — match where `_load_positions_section` is tested):

1. `test_closed_trades_orders_and_summary`: seed 3 closed positions (2 winners,
   1 loser) with distinct `closed_at` → assert rows are exit-date DESC, `days_held`
   correct, per-row `return_pct` correct, summary `total_count=3`,
   `realized_pnl_total` = Σ, `win_rate = 2/3`, `avg_return_pct` = mean of the 3.
2. `test_closed_trades_zero_cost_return_none`: seed a closed position with
   `entry_price=0` (or qty 0) → its `return_pct is None` and it's excluded from
   `avg_return_pct`.
3. `test_closed_trades_empty`: no closed positions → `section_ok` with empty rows
   and empty message; summary `total_count=0`, `win_rate is None`,
   `avg_return_pct is None`.
4. `test_closed_trades_cap_50`: seed 55 closed → `rows` length 50,
   `summary.total_count == 55`, `count_label` = "Showing latest 50 of 55 closed
   trades".

**Route test** (`tests/web/test_paper_trading*` or wherever the dashboard route is
tested): seed a couple closed positions → `GET /lab/paper-trading` (authed) renders
the "Closed Trades" section with the ticker + a realized-P&L value; and with no
closed positions renders the empty-state text.

**Architecture guard:** the presenter is DB-only; if there's an existing
zero-network guard test over `query_models.py`, it continues to pass (no new
imports of network clients).

## Files touched

- `marketpulse/trading/query_models.py` — 3 dataclasses, `_load_closed_trades_section`,
  `PaperTradingDashboard.closed_trades` field, wiring in builder + error dashboard.
- `marketpulse/web/templates/lab_paper_trading.html` — Closed Trades section.
- `tests/...` — presenter tests + route test.

No DB migration (reads existing `paper_position` columns). No new route. No deps.
