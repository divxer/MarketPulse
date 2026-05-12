# Trades Page Fixes — Design Spec

**Status:** Approved, ready for implementation plan
**Author:** harvey
**Date:** 2026-05-12

## Goal

Fix three user-reported issues on `/trades`:
1. Selecting 买入 sometimes records the trade as 卖出 (action drift across form resets)
2. Date field is silently required on the front-end even though the back-end treats blank as "today"
3. No way to edit an existing trade

## Bug 1: action drift after form.reset()

### Root cause

`trades.html` form has a hidden `<input name="action">` dynamically created by `onEventKindChange(kind)` to carry `buy`/`sell` to the POST endpoint. After successful submit, `hx-on::after-request="if(event.detail.successful) this.reset()"` calls `form.reset()`. `form.reset()` resets the visible `<select id="event-kind">` to its first option (`buy`) **but does not fire the `change` event**, so `onEventKindChange` is never re-invoked. The hidden action input retains its previous value (`sell` if the user just submitted a sell).

Empirical scenario:
1. User selects 卖出 → `onEventKindChange('sell')` → hidden action='sell'
2. Submit → record stored as `sell` ✓
3. `form.reset()` → select visually back to 买入, **hidden action still 'sell'**
4. User fills the form again (dropdown shows 买入), submits → server receives `action=sell` → 卖出 record

### Fix

Replace the `hx-on::after-request` attribute so it resets and then re-syncs:

```html
hx-on::after-request="if(event.detail.successful){this.reset();onEventKindChange(document.getElementById('event-kind').value);}"
```

One-line behavioral change. The hidden action input is reconstructed (via the existing branch in `onEventKindChange`) to match the now-reset select value.

### Tests

`tests/web/test_trades.py` — add a regression test asserting the POST endpoint correctly stores the `action` field as provided. The bug was front-end only, but we can also add a small DOM-state assertion via a template-snippet test (the rendered HTML contains the `hx-on::after-request` text). That at least catches accidental removal.

## Bug 2: date field is silently required

### Root cause

`<input name="executed_at" type="date" class="border rounded px-3 py-1 trade-field">` is one of `.trade-field` elements. `onEventKindChange` marks every `.trade-field` as `required = true` when kind is `buy`/`sell`. The browser then refuses to submit with an empty date. The back-end (`trades_add` route line 101) already accepts blank `executed_at` and substitutes `datetime.now(UTC)` — so the front-end is artificially stricter than the back-end.

### Fix

Add `data-optional="true"` to the `executed_at` input. Skip the `required = true` line for elements with that attribute in `onEventKindChange`. Title text already says "留空=今天" — the behavior now matches the documentation.

Template change:
```html
<input name="executed_at" type="date" title="交易日期 (留空=今天)"
       data-optional="true"
       class="border rounded px-3 py-1 trade-field" />
```

JS change (in `onEventKindChange`):
```javascript
showGroup.forEach(el => {
  el.classList.remove('hidden');
  if (!el.dataset.optional) el.required = true;
});
```

### Tests

`tests/web/test_trades.py::test_trade_post_blank_executed_at_defaults_to_now` already verifies the back-end accepts blank. Add a template-snippet assertion that the `executed_at` input has `data-optional="true"` so a future refactor can't silently drop it.

## Bug 3: cannot edit a trade

### Approach: full edit, recompute on save, reuse the existing form via prefill

**Why full edit, not just-notes:** if the user mis-typed a price or quantity, only updating notes is useless. Restricting to notes-only would leave the more common error case (wrong number) unsupported.

**Why recompute the ticker on every edit:** changing price, quantity, action, ticker, fees, or date affects holding/realized_pl. Rather than patch a single field's impact (fragile, easy to get wrong), reuse `recompute_ticker(db, ticker)` which walks all events for a ticker and rebuilds the Holding row + realized_pl values from scratch. This is the same function that `trades_delete` calls — proven correct in tests. Cost is one full walk per edit; trade history is small (typically < 1000 events per ticker), so this is negligible.

**Why prefill the existing form (option iii):** smallest code change. Adding an inline-edit row would mean a parallel form-rendering path inside the table partial. A modal would mean a new template and JS open/close logic. Prefilling the existing form requires only:
- An "Edit" button per trade row that fills the form fields and switches the form into "edit mode" (visually + hx-target)
- A `PUT /trades/{id}` endpoint that takes the same form fields as POST
- A "Cancel" button that empties the form and exits edit mode

### Components

**Template `partials/trades_table.html`** — add Edit button next to Delete for trade rows. The button carries the trade's data as `data-*` attributes and calls a JS function `loadTradeIntoForm(...)` that prefills the form, switches `hx-post` to `hx-put`, sets the target trade id on a hidden input, and shows a Cancel button.

**Template `trades.html`** — add:
- Hidden `<input name="trade_id">` (empty by default; set when editing)
- "Cancel edit" button (`hidden` by default; shown when editing)
- "记录" button label changes to "保存修改" when editing
- JS: `loadTradeIntoForm(t)` and `exitEditMode()`

**Route `marketpulse/web/routes/trades.py`**:

```python
@router.put("/trades/{trade_id}", response_class=HTMLResponse)
def trades_update(
    request: Request,
    trade_id: int,
    ticker: str = Form(...),
    action: str = Form(...),
    quantity: float = Form(...),
    price: float = Form(...),
    fees: float = Form(0.0),
    notes: str = Form(""),
    executed_at: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Edit an existing trade. If ticker changed, recompute BOTH the old
    and new tickers. Otherwise recompute the one ticker."""
```

Implementation:
1. Look up the trade by id (404 if not found)
2. Validate inputs (same `_TICKER_RE`, same `executed_at` parsing as POST)
3. Remember `old_ticker = trade.ticker` before mutation
4. Mutate `trade.ticker / action / quantity / price / fees / notes / executed_at` in place
5. `db.commit()`
6. `recompute_ticker(db, old_ticker)` — if old_ticker differs from new ticker, also `recompute_ticker(db, new_ticker)`
7. Return the re-rendered timeline (same logic as `trades_add` does post-insert)

**JS** in `trades.html`:

```javascript
function loadTradeIntoForm(data) {
  const form = document.getElementById('event-form');
  // data: {id, ticker, action, quantity, price, fees, executed_at, notes}
  form.querySelector('[name="ticker"]').value = data.ticker;
  form.querySelector('[name="quantity"]').value = data.quantity;
  form.querySelector('[name="price"]').value = data.price;
  form.querySelector('[name="fees"]').value = data.fees;
  form.querySelector('[name="notes"]').value = data.notes || '';
  form.querySelector('[name="executed_at"]').value = data.executed_at || '';
  document.getElementById('event-kind').value = data.action;       // 'buy' or 'sell'
  onEventKindChange(data.action);                                   // syncs hidden action input + visible fields
  document.getElementById('trade-id-input').value = data.id;
  form.setAttribute('hx-put', `/trades/${data.id}`);
  form.removeAttribute('hx-post');
  document.getElementById('submit-btn').textContent = '保存修改';
  document.getElementById('cancel-edit-btn').classList.remove('hidden');
  form.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function exitEditMode() {
  const form = document.getElementById('event-form');
  form.reset();
  document.getElementById('trade-id-input').value = '';
  form.removeAttribute('hx-put');
  form.setAttribute('hx-post', '/trades');
  document.getElementById('submit-btn').textContent = '记录';
  document.getElementById('cancel-edit-btn').classList.add('hidden');
  onEventKindChange(document.getElementById('event-kind').value);
}
```

The existing `hx-on::after-request` reset hook also calls `exitEditMode()` on successful submit (whether add or edit) so the form is always in "add" mode after any successful action.

### Edge cases

| Case | Behavior |
|---|---|
| Edit a trade so it becomes oversell (e.g., changed quantity > remaining holdings) | `recompute_ticker` doesn't validate "oversell" — it just walks events. If the timeline becomes invalid (negative quantity at some point), the recompute may produce a wrong Holding or negative `realized_pl`. **Mitigation:** Before saving, the route calls `_walk_events` in dry-run mode. If at any point in the walk the running quantity would go negative, return 422 with a clear error. (Simpler alternative: skip this check — the existing `record_trade` validation only blocks on insert, not on edit. We accept the risk that an edit could produce a self-inconsistent history; the user is the source of truth.) **Decision:** skip the pre-check for v1. If a user edits into an inconsistent state, the next normal `record_trade` will refuse because of the oversell check, surfacing the issue. |
| Ticker changes during edit (rare but possible: typo'd ticker on initial entry) | Recompute both old and new ticker. |
| Edit a split or dividend row | Out of scope for v1. Splits/dividends have their own routes. The Edit button only appears on Trade rows. |
| Concurrent edit (user open in 2 tabs) | Last write wins. Acceptable for a single-user tool. |
| Edit removes the user's only buy, leaving sells with no basis | Same as the oversell case — recompute may produce odd values. Accepted risk. |

### Tests

`tests/web/test_trades.py` — new tests:
- `test_trades_update_basic`: PUT /trades/{id} with new price; verify trade row updated and holding's avg_cost reflects new price
- `test_trades_update_ticker_change`: changes ticker; both old and new ticker holdings updated
- `test_trades_update_404_unknown_id`: PUT for nonexistent id returns 404
- `test_trades_update_invalid_ticker_422`: PUT with bad ticker returns 422
- `test_trades_update_recomputes_realized_pl`: edit a buy that earlier sell depended on; realized_pl of that sell changes

## Out of Scope

- Editing splits and dividends (those have their own routes; can be added later if needed)
- Audit trail / edit history (no `updated_at` column added; would require a migration)
- Bulk edit / batch operations
- Undo-edit (just edit again to revert)
- Per-field validation that compares to current holdings to predict invalidity (deferred — see Edge cases)

## File Manifest

**Modified:**
- `marketpulse/web/templates/trades.html` — JS fix for bug 1, data-optional for bug 2, edit/cancel UI for bug 3, hidden trade_id input, JS for loadTradeIntoForm/exitEditMode
- `marketpulse/web/templates/partials/trades_table.html` — Edit button on trade rows with data-* payload
- `marketpulse/web/routes/trades.py` — new `PUT /trades/{trade_id}` endpoint
- `tests/web/test_trades.py` — regression tests for bug 1 & 2, new tests for edit

**Unchanged:**
- `marketpulse/holdings/trades.py` — `record_trade`, `recompute_ticker` reused as-is
- `marketpulse/db/models.py::Trade` — no schema change (no `updated_at`, no audit table)

## Risk

**Low for bugs 1 and 2.** Each is a 1-3 line fix to a single template, behavior already covered by existing back-end logic.

**Medium for bug 3.** New endpoint + new UI flow, but the heavy lifting (`recompute_ticker`) is already proven by the delete path. Edit mode adds JS state that needs to be carefully entered/exited — `exitEditMode()` is called from both Cancel and successful-submit hooks to keep this robust.
