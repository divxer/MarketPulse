# Trades Page Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan.

**Goal:** Fix three trade-page bugs: action drift after form reset, falsely-required date field, no edit capability.

**Architecture:** Bugs 1 & 2 are 1-3 line edits to `trades.html`. Bug 3 adds a `PUT /trades/{id}` endpoint, an Edit button per row, and a JS edit-mode toggle that prefills the existing form. Heavy lifting reused: `recompute_ticker` (already proven by delete path).

**Tech Stack:** FastAPI, HTMX, vanilla JS, Jinja2, SQLAlchemy. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-05-12-trades-page-fixes-design.md`](../specs/2026-05-12-trades-page-fixes-design.md)

**Branch:** new `fix/trades-page` off latest `main`.

---

## Pre-flight

- [ ] **Step 0a: Branch + baseline tests**

```bash
git checkout main && git pull
git checkout -b fix/trades-page
uv run pytest 2>&1 | tail -3
```
Expected: `293 passed`. Stop if not.

---

## Task 1: Fix bug 1 (action drift) — one-line JS hook

**Files:** `marketpulse/web/templates/trades.html`

- [ ] **Step 1a: Write the failing test**

Add to `tests/web/test_trades.py` (append at end):

```python
def test_trades_form_after_request_resyncs_action(client: TestClient, monkeypatch):
    """Regression for the bug where form.reset() after submit left the hidden
    `action` input at its previous value, causing the next submission to use
    the stale action even though the visible select showed a different one.
    The fix is in the template's `hx-on::after-request` attribute, which
    must call onEventKindChange() after this.reset()."""
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert r.status_code == 200
    body = r.text
    # The attribute must include a call back into onEventKindChange after reset
    assert "this.reset()" in body
    assert "onEventKindChange" in body
    # Specifically, the after-request hook must re-sync (the JS function call
    # must appear inside the hx-on::after-request expression):
    assert "hx-on::after-request" in body
    # Crude but effective: the two pieces must be in the same attribute value.
    import re
    m = re.search(r'hx-on::after-request="([^"]+)"', body)
    assert m is not None, "hx-on::after-request attribute missing"
    expr = m.group(1)
    assert "this.reset()" in expr
    assert "onEventKindChange" in expr
```

- [ ] **Step 1b: Run the test, confirm it fails**

```bash
uv run pytest tests/web/test_trades.py::test_trades_form_after_request_resyncs_action -v
```
Expected: FAIL with `assert "onEventKindChange" in expr` (the current attribute only resets, doesn't re-sync).

- [ ] **Step 1c: Apply the fix**

In `marketpulse/web/templates/trades.html`, find:

```html
        hx-on::after-request="if(event.detail.successful) this.reset()"
```

Replace with (one line):

```html
        hx-on::after-request="if(event.detail.successful){this.reset();onEventKindChange(document.getElementById('event-kind').value);}"
```

- [ ] **Step 1d: Verify the test passes**

```bash
uv run pytest tests/web/test_trades.py::test_trades_form_after_request_resyncs_action -v
```
Expected: PASS.

Also re-run the full trade test file:

```bash
uv run pytest tests/web/test_trades.py -v 2>&1 | tail -10
```
Expected: all green.

- [ ] **Step 1e: Commit**

```bash
git add marketpulse/web/templates/trades.html tests/web/test_trades.py
git commit -m "$(cat <<'EOF'
fix(trades): re-sync hidden action input after form reset

form.reset() resets the visible <select id="event-kind"> to its first
option (buy) but does NOT fire the change event. The hidden
<input name="action"> — created and managed by onEventKindChange — was
therefore left at its previous value. After submitting a sell, the
next submit would silently carry action=sell even though the dropdown
showed 买入.

Fix: extend hx-on::after-request to also call onEventKindChange(...) so
the hidden action input is rebuilt to match the now-reset select value.

Test asserts the after-request attribute contains both this.reset() and
onEventKindChange, guarding against accidental removal of either half.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Fix bug 2 (date field silently required)

**Files:** `marketpulse/web/templates/trades.html`

- [ ] **Step 2a: Write the failing test**

Append to `tests/web/test_trades.py`:

```python
def test_trade_form_executed_at_is_optional(client: TestClient, monkeypatch):
    """The date input is documented as 'blank = today' and the backend
    accepts blank. The template must NOT mark it as required via the
    onEventKindChange JS — it carries data-optional="true" which the JS
    must skip when setting required."""
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert r.status_code == 200
    body = r.text
    # The executed_at input must have data-optional="true".
    import re
    m = re.search(
        r'<input\s+name="executed_at"[^>]*data-optional="true"', body,
    )
    assert m is not None, (
        "executed_at input must have data-optional=\"true\" "
        "(so onEventKindChange skips required=true on it)"
    )
    # The JS function must check dataset.optional before setting required.
    assert "dataset.optional" in body, (
        "onEventKindChange must check dataset.optional to honor the flag"
    )
```

- [ ] **Step 2b: Run and confirm failure**

```bash
uv run pytest tests/web/test_trades.py::test_trade_form_executed_at_is_optional -v
```
Expected: FAIL.

- [ ] **Step 2c: Apply the template fix**

In `marketpulse/web/templates/trades.html`, find:

```html
    <input name="executed_at" type="date" title="交易日期 (留空=今天)"
           class="border rounded px-3 py-1 trade-field" />
```

Replace with:

```html
    <input name="executed_at" type="date" title="交易日期 (留空=今天)"
           data-optional="true"
           class="border rounded px-3 py-1 trade-field" />
```

Also in the same file, find the `onEventKindChange` function and update the `showGroup.forEach` line. Current:

```javascript
      showGroup.forEach(el => { el.classList.remove('hidden'); el.required = true; });
```

Replace with:

```javascript
      showGroup.forEach(el => {
        el.classList.remove('hidden');
        if (!el.dataset.optional) el.required = true;
      });
```

- [ ] **Step 2d: Verify test passes**

```bash
uv run pytest tests/web/test_trades.py::test_trade_form_executed_at_is_optional -v
```
Expected: PASS.

Full file sanity:

```bash
uv run pytest tests/web/test_trades.py 2>&1 | tail -5
```
Expected: all green.

- [ ] **Step 2e: Commit**

```bash
git add marketpulse/web/templates/trades.html tests/web/test_trades.py
git commit -m "$(cat <<'EOF'
fix(trades): date field is optional, no longer falsely required

The executed_at input had class="trade-field", and onEventKindChange
marked every trade-field as required=true. The title text already
documented 'blank = today' and the backend already substitutes
datetime.now(UTC) for blank input — the front-end was artificially
stricter than the back-end.

Fix: add data-optional="true" to the input. onEventKindChange now skips
setting required on elements carrying that attribute. Other trade fields
(quantity, price, fees) remain required.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add PUT /trades/{id} endpoint

**Files:** `marketpulse/web/routes/trades.py`, `tests/web/test_trades.py`

- [ ] **Step 3a: Write the first failing test (basic update)**

Append to `tests/web/test_trades.py`:

```python
def test_trades_update_basic(client: TestClient, monkeypatch):
    """Editing a trade updates its fields and recomputes the ticker holding."""
    _login(client, monkeypatch)
    # Create a buy
    r = client.post("/trades", data={
        "ticker": "AAPL", "action": "buy",
        "quantity": 10, "price": 100.0, "fees": 0,
    })
    assert r.status_code == 200
    # Look up the trade we just made
    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade, Holding
    s = next(db_base.session_scope())
    trade_id = s.query(Trade).filter(Trade.ticker == "AAPL").one().id
    # Edit it: change price from 100 to 120
    r = client.put(f"/trades/{trade_id}", data={
        "ticker": "AAPL", "action": "buy",
        "quantity": 10, "price": 120.0, "fees": 0,
    })
    assert r.status_code == 200
    # Verify the trade and the holding now reflect the new price
    s2 = next(db_base.session_scope())
    t = s2.query(Trade).filter(Trade.id == trade_id).one()
    assert t.price == 120.0
    h = s2.query(Holding).filter(Holding.ticker == "AAPL").one()
    assert h.avg_cost == 120.0  # single buy, avg = price


def test_trades_update_404_unknown_id(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.put("/trades/99999", data={
        "ticker": "AAPL", "action": "buy", "quantity": 1, "price": 1.0,
    })
    assert r.status_code == 404


def test_trades_update_invalid_ticker_422(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.post("/trades", data={
        "ticker": "AAPL", "action": "buy",
        "quantity": 1, "price": 100.0,
    })
    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    trade_id = s.query(Trade).filter(Trade.ticker == "AAPL").one().id
    r = client.put(f"/trades/{trade_id}", data={
        "ticker": "bad ticker with spaces!", "action": "buy",
        "quantity": 1, "price": 100.0,
    })
    assert r.status_code == 422


def test_trades_update_ticker_change_recomputes_both(client: TestClient, monkeypatch):
    """Changing the ticker on an edit must recompute both the old and new
    ticker holdings."""
    _login(client, monkeypatch)
    # Create AAPL buy
    client.post("/trades", data={
        "ticker": "AAPL", "action": "buy",
        "quantity": 5, "price": 100.0,
    })
    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade, Holding
    s = next(db_base.session_scope())
    trade_id = s.query(Trade).filter(Trade.ticker == "AAPL").one().id
    # Edit: change ticker AAPL → MSFT
    r = client.put(f"/trades/{trade_id}", data={
        "ticker": "MSFT", "action": "buy",
        "quantity": 5, "price": 100.0,
    })
    assert r.status_code == 200
    s2 = next(db_base.session_scope())
    # AAPL holding gone, MSFT holding present
    assert s2.query(Holding).filter(Holding.ticker == "AAPL").one_or_none() is None
    msft = s2.query(Holding).filter(Holding.ticker == "MSFT").one()
    assert msft.quantity == 5
```

- [ ] **Step 3b: Run, confirm all fail**

```bash
uv run pytest tests/web/test_trades.py -k "test_trades_update_" -v
```
Expected: 4 FAIL with `405 Method Not Allowed` (PUT route doesn't exist yet).

- [ ] **Step 3c: Implement PUT /trades/{id}**

In `marketpulse/web/routes/trades.py`, find the `trades_delete` function. Add this new endpoint AFTER `trades_add` and BEFORE `trades_delete`:

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
    """Edit an existing trade. Mutates the row in place, then runs
    recompute_ticker for the affected ticker(s) so Holding + realized_pl
    are rebuilt from the full event history.

    Validation mirrors trades_add (POST). If the ticker changes, both
    the old and new ticker are recomputed."""
    trade = db.query(Trade).filter(Trade.id == trade_id).one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="trade not found")

    normalized = ticker.strip().upper()
    if not _TICKER_RE.match(normalized):
        raise HTTPException(status_code=422, detail="invalid ticker")

    action_norm = action.lower().strip()
    if action_norm not in ("buy", "sell"):
        raise HTTPException(
            status_code=422,
            detail=f"invalid action {action!r}, must be 'buy' or 'sell'",
        )
    if quantity <= 0:
        raise HTTPException(status_code=422, detail="quantity must be positive")
    if price < 0:
        raise HTTPException(status_code=422, detail="price cannot be negative")
    if fees < 0:
        raise HTTPException(status_code=422, detail="fees cannot be negative")

    # Same date parsing as trades_add.
    executed_at_dt: datetime
    if executed_at.strip():
        try:
            s = executed_at.strip()
            if len(s) == 10:
                executed_at_dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)
            else:
                executed_at_dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if executed_at_dt.tzinfo is None:
                    executed_at_dt = executed_at_dt.replace(tzinfo=UTC)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid executed_at: {exc}",
            ) from exc
    else:
        executed_at_dt = datetime.now(UTC)

    old_ticker = trade.ticker

    trade.ticker = normalized
    trade.action = action_norm
    trade.quantity = quantity
    trade.price = price
    trade.fees = fees
    trade.executed_at = executed_at_dt
    trade.notes = notes or None
    db.commit()

    # Recompute the old ticker first (so its Holding reflects the removal
    # of this trade), then the new ticker (to apply the trade there).
    # When ticker is unchanged, the second call is a no-op (same ticker).
    recompute_ticker(db, old_ticker)
    if normalized != old_ticker:
        recompute_ticker(db, normalized)

    # Re-render the timeline (same logic as trades_add).
    events: list[dict] = []
    for t in db.query(Trade).all():
        when = t.executed_at or t.created_at
        events.append({"kind": "trade", "when": when, "obj": t})
    _EOD = time(23, 59, 59, tzinfo=UTC)
    for s in db.query(StockSplit).all():
        events.append({"kind": "split", "when": datetime.combine(s.ex_date, _EOD), "obj": s})
    for d in db.query(Dividend).all():
        events.append({"kind": "dividend", "when": datetime.combine(d.ex_date, _EOD), "obj": d})
    events.sort(key=lambda e: e["when"], reverse=True)
    events = events[:200]

    return templates.TemplateResponse(
        request,
        "partials/trades_table.html",
        {
            "events": events,
            "filter_ticker": None,
            "filter_event_type": None,
            "realized_pl_total": total_realized_pl(db),
        },
    )
```

- [ ] **Step 3d: Verify tests pass**

```bash
uv run pytest tests/web/test_trades.py -k "test_trades_update_" -v
```
Expected: 4 PASS.

Full file:
```bash
uv run pytest tests/web/test_trades.py 2>&1 | tail -5
```
Expected: all green.

- [ ] **Step 3e: Commit**

```bash
git add marketpulse/web/routes/trades.py tests/web/test_trades.py
git commit -m "$(cat <<'EOF'
feat(trades): PUT /trades/{id} for editing existing trades

Validates inputs identically to POST /trades (ticker regex, action
must be buy/sell, positive quantity, non-negative price/fees, date
parsing). Mutates the trade row in place, then calls recompute_ticker
for the affected ticker(s) — reusing the same machinery that
trades_delete uses to rebuild Holding + realized_pl from history.

If the ticker changes, recomputes both the old and new tickers.

Returns the re-rendered timeline partial (same shape as the POST
endpoint) so HTMX can swap the table.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add Edit button + prefill UI

**Files:** `marketpulse/web/templates/partials/trades_table.html`, `marketpulse/web/templates/trades.html`

- [ ] **Step 4a: Add Edit button on trade rows**

In `marketpulse/web/templates/partials/trades_table.html`, find the `<td class="text-right">` block inside the trade row (`{% if e.kind == "trade" %}` branch). Currently:

```html
          <td class="text-right">
            <button
              hx-delete="/trades/{{ t.id }}"
              hx-target="#trade-row-{{ t.id }}"
              hx-swap="outerHTML"
              hx-confirm="删除这笔交易?会自动重算该代码的持仓和已实现盈亏。"
              class="text-red-600 text-xs hover:underline">删除</button>
          </td>
```

Replace with:

```html
          <td class="text-right">
            <button
              type="button"
              onclick='loadTradeIntoForm({{ {
                "id": t.id,
                "ticker": t.ticker,
                "action": t.action,
                "quantity": t.quantity,
                "price": t.price,
                "fees": t.fees,
                "notes": t.notes or "",
                "executed_at": (t.executed_at.strftime("%Y-%m-%d") if t.executed_at else "")
              } | tojson }})'
              class="text-blue-600 text-xs hover:underline mr-2">编辑</button>
            <button
              hx-delete="/trades/{{ t.id }}"
              hx-target="#trade-row-{{ t.id }}"
              hx-swap="outerHTML"
              hx-confirm="删除这笔交易?会自动重算该代码的持仓和已实现盈亏。"
              class="text-red-600 text-xs hover:underline">删除</button>
          </td>
```

- [ ] **Step 4b: Add hidden trade_id input + cancel button + submit-btn id to form**

In `marketpulse/web/templates/trades.html`, find the form fields and the submit button. Update the submit button and add a hidden trade_id input + a cancel button. Current submit:

```html
    <button class="bg-slate-900 text-white px-3 py-1 rounded">记录</button>
  </form>
```

Replace the submit-button line and add the trade_id + cancel button to it:

```html
    <input type="hidden" name="trade_id" id="trade-id-input" value="" />
    <button id="submit-btn" type="submit"
            class="bg-slate-900 text-white px-3 py-1 rounded">记录</button>
    <button id="cancel-edit-btn" type="button" onclick="exitEditMode()"
            class="bg-slate-200 text-slate-700 px-3 py-1 rounded hidden">取消编辑</button>
  </form>
```

- [ ] **Step 4c: Add the JS functions**

In `marketpulse/web/templates/trades.html`, find the existing `<script>` block (the one with `onEventKindChange`). At the end of that script block (just before `</script>`), add:

```javascript
    function loadTradeIntoForm(data) {
      const form = document.getElementById('event-form');
      form.querySelector('[name="ticker"]').value = data.ticker || '';
      form.querySelector('[name="quantity"]').value = data.quantity ?? '';
      form.querySelector('[name="price"]').value = data.price ?? '';
      form.querySelector('[name="fees"]').value = data.fees ?? 0;
      form.querySelector('[name="notes"]').value = data.notes || '';
      form.querySelector('[name="executed_at"]').value = data.executed_at || '';
      document.getElementById('event-kind').value = data.action;
      onEventKindChange(data.action);
      document.getElementById('trade-id-input').value = data.id;
      form.setAttribute('hx-put', '/trades/' + data.id);
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

- [ ] **Step 4d: Update the after-request hook to exit edit mode**

In `marketpulse/web/templates/trades.html`, find:

```html
        hx-on::after-request="if(event.detail.successful){this.reset();onEventKindChange(document.getElementById('event-kind').value);}"
```

Replace with:

```html
        hx-on::after-request="if(event.detail.successful) exitEditMode();"
```

(`exitEditMode` already calls `form.reset()` and `onEventKindChange`, so this fully subsumes the Task 1 fix while also resetting hx-post/hx-put, the submit button text, and the cancel button visibility.)

- [ ] **Step 4e: Write tests for the Edit UI presence**

Append to `tests/web/test_trades.py`:

```python
def test_trades_table_has_edit_button(client: TestClient, monkeypatch):
    """After creating a trade, the rendered timeline must include an Edit
    button whose onclick payload carries the trade fields."""
    _login(client, monkeypatch)
    r = client.post("/trades", data={
        "ticker": "AAPL", "action": "buy",
        "quantity": 5, "price": 100.0,
    })
    assert r.status_code == 200
    r = client.get("/trades")
    assert r.status_code == 200
    body = r.text
    assert "loadTradeIntoForm" in body, "Edit button JS call missing"
    assert "编辑" in body, "Edit button label missing"
    assert "&quot;ticker&quot;: &quot;AAPL&quot;" in body or '"ticker": "AAPL"' in body
    assert "exitEditMode" in body, "exitEditMode function missing"
    assert 'id="trade-id-input"' in body, "trade_id input missing"
    assert 'id="cancel-edit-btn"' in body, "cancel button missing"
```

- [ ] **Step 4f: Run all trade tests**

```bash
uv run pytest tests/web/test_trades.py -v 2>&1 | tail -20
```
Expected: all green (including the new test).

Also re-run the bug-1 regression test — Task 4 changed the `hx-on::after-request` attribute so it now contains `exitEditMode` instead of the literal `this.reset()` + `onEventKindChange`. Update the bug-1 test to match: change its assertions to look for `exitEditMode` in the attribute (since `exitEditMode` internally calls both).

Find in `tests/web/test_trades.py`:

```python
    expr = m.group(1)
    assert "this.reset()" in expr
    assert "onEventKindChange" in expr
```

Replace with:

```python
    expr = m.group(1)
    assert "exitEditMode" in expr, (
        "after-request must call exitEditMode (which internally resets and "
        "re-syncs the hidden action input via onEventKindChange)"
    )
```

Run again:

```bash
uv run pytest tests/web/test_trades.py -v 2>&1 | tail -20
```
Expected: all green.

- [ ] **Step 4g: Full project tests pass**

```bash
uv run pytest 2>&1 | tail -3
```
Expected: all passing (293 + new tests).

- [ ] **Step 4h: Commit**

```bash
git add marketpulse/web/templates/trades.html marketpulse/web/templates/partials/trades_table.html tests/web/test_trades.py
git commit -m "$(cat <<'EOF'
feat(trades): edit trade via prefilled form, with auto-recompute on save

UI flow:
- Each trade row gets an "编辑" button. Click → form is prefilled with
  that trade's fields, submit button label changes to "保存修改", an
  cancel button appears, and the form switches from hx-post to
  hx-put /trades/{id}.
- "取消编辑" exits edit mode without saving.
- A successful submit (whether add or edit) calls exitEditMode which
  resets the form, clears the trade_id, restores hx-post, and re-runs
  onEventKindChange to keep the hidden action input in sync. This
  subsumes the hx-on hook from earlier in this branch.

The backend was added in the previous commit (PUT /trades/{id}); this
commit is the UI layer.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Push and open PR

- [ ] **Step 5a: Push**

```bash
git push -u origin fix/trades-page
```

- [ ] **Step 5b: Open PR**

```bash
gh pr create --title "fix(trades): action drift, optional date, and edit support" --body "$(cat <<'EOF'
## Summary

Three user-reported issues on \`/trades\`:

1. **action drift after submit** — selecting 买入 sometimes recorded as 卖出.
   Root cause: \`form.reset()\` after a successful submit reset the visible select to 买入 but didn't fire its \`change\` event, so the hidden \`<input name=\"action\">\` (created and updated by \`onEventKindChange\`) kept its previous value. Fix: after-request hook now calls \`exitEditMode()\` which resets *and* re-syncs the hidden input.

2. **date field falsely required** — backend has accepted blank \`executed_at\` (and substituted \`datetime.now(UTC)\`) since the field was added, but \`onEventKindChange\` was marking every \`.trade-field\` as \`required=true\`. Fix: \`data-optional=\"true\"\` on the date input; JS skips required for those.

3. **no edit support** — only Delete was available. New: \`PUT /trades/{id}\` endpoint (mirrors POST validation) + Edit button per row that prefills the form. \`recompute_ticker\` rebuilds Holding + realized_pl after every save, reusing the same machinery as Delete. Ticker change on edit recomputes both old and new tickers.

Spec: \`docs/superpowers/specs/2026-05-12-trades-page-fixes-design.md\`

## Test Plan

- [x] All existing tests pass
- [x] New: \`test_trades_form_after_request_resyncs_action\` (bug 1)
- [x] New: \`test_trade_form_executed_at_is_optional\` (bug 2)
- [x] New: \`test_trades_update_basic\`, \`test_trades_update_404_unknown_id\`, \`test_trades_update_invalid_ticker_422\`, \`test_trades_update_ticker_change_recomputes_both\` (bug 3 backend)
- [x] New: \`test_trades_table_has_edit_button\` (bug 3 UI)
- [ ] Manual after deploy:
  - [ ] On /trades, alternate 买入 / 卖出 across several submits; verify each record matches the selected action
  - [ ] Submit a trade with the date field empty; verify the record gets today's date
  - [ ] Click 编辑 on an existing trade; verify form prefills correctly, submit changes the row, holding/realized_pl recompute
  - [ ] Click 编辑 then 取消编辑; verify form clears and is back in add mode
  - [ ] Edit a trade and change its ticker; verify old ticker's holding goes away and new ticker's appears

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5c: Self-review checklist**

Run each:
- `git log --oneline | head -6` — 4 commits ahead of main (Tasks 1-4)
- `uv run pytest 2>&1 | tail -3` — all green
- `uv run ruff check 2>&1 | tail -3` — clean
- `grep -c 'loadTradeIntoForm\|exitEditMode' marketpulse/web/templates/trades.html` — at least 4 (each function defined + called from after-request)
- `grep -c 'PUT.*trades' marketpulse/web/routes/trades.py` — at least 1 (the route decorator)
- `grep -c 'data-optional' marketpulse/web/templates/trades.html` — at least 1

Report the PR URL.
