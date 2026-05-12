# Trade Date TZ-Aware Implementation Plan (revised)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** TZ-aware date parsing + preserve original timestamp on edits-without-date-changes + display times in user's local TZ.

**Architecture:** A shared backend helper `_parse_executed_at` with three branches (preserve-original / blank-now / date-combine / full-ISO). Two new hidden form fields (`tz_offset_minutes`, `original_executed_at_iso`). Display layer uses `<time data-utc>` + JS conversion on load and after HTMX swaps.

**Tech Stack:** FastAPI, vanilla JS, HTMX, Jinja2.

**Spec:** [`docs/superpowers/specs/2026-05-12-trade-date-tz-aware-design.md`](../specs/2026-05-12-trade-date-tz-aware-design.md)

**Branch:** continue on `fix/trades-page`.

---

## Pre-flight

- [ ] **Step 0a:** `git branch --show-current` → `fix/trades-page`; `git status --short` → empty; `uv run pytest 2>&1 | tail -3` → 300 passed.

---

## Task 1: Backend `_parse_executed_at` helper with original-preservation

**Files:** `marketpulse/web/routes/trades.py`, `tests/web/test_trades.py`

- [ ] **Step 1a: Write 5 failing tests**

Append to `tests/web/test_trades.py`:

```python
def test_trade_post_with_tz_offset_combines_with_local_now(client: TestClient, monkeypatch):
    """POST with tz_offset_minutes + YYYY-MM-DD: stored UTC, when shifted into
    user's local TZ, lands on the chosen date."""
    from datetime import UTC, timedelta
    _login(client, monkeypatch)
    tz_offset = -480  # Beijing UTC+8
    res = client.post("/trades", data={
        "ticker": "TZA", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": "2026-05-12", "tz_offset_minutes": str(tz_offset),
    })
    assert res.status_code == 200
    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    t = s.query(Trade).filter(Trade.ticker == "TZA").one()
    stored = t.executed_at if t.executed_at.tzinfo else t.executed_at.replace(tzinfo=UTC)
    local_dt = stored - timedelta(minutes=tz_offset)
    assert local_dt.date().isoformat() == "2026-05-12"


def test_trade_post_zero_tz_offset_uses_now_time_of_day(client: TestClient, monkeypatch):
    """With tz_offset=0 (UTC client), YYYY-MM-DD picks current UTC time-of-day,
    not the old midnight default."""
    from datetime import datetime as _dt
    _login(client, monkeypatch)
    res = client.post("/trades", data={
        "ticker": "TZB", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": "2026-05-12", "tz_offset_minutes": "0",
    })
    assert res.status_code == 200
    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    t = s.query(Trade).filter(Trade.ticker == "TZB").one()
    assert (t.executed_at.year, t.executed_at.month, t.executed_at.day) == (2026, 5, 12)
    assert t.executed_at.time() != _dt.min.time(), (
        "TZ-aware parsing must use current time-of-day, not arbitrary 00:00"
    )


def test_trade_post_blank_date_unchanged_by_tz_offset(client: TestClient, monkeypatch):
    """Blank executed_at + any tz_offset → still datetime.now(UTC)."""
    from datetime import UTC, datetime
    _login(client, monkeypatch)
    before = datetime.now(UTC)
    res = client.post("/trades", data={
        "ticker": "TZC", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": "", "tz_offset_minutes": "-480",
    })
    after = datetime.now(UTC)
    assert res.status_code == 200
    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    t = s.query(Trade).filter(Trade.ticker == "TZC").one()
    stored = t.executed_at if t.executed_at.tzinfo else t.executed_at.replace(tzinfo=UTC)
    assert before <= stored <= after


def test_trades_update_preserves_original_when_date_unchanged(client: TestClient, monkeypatch):
    """PUT /trades/{id} with date unchanged + original_executed_at_iso provided
    must preserve the original timestamp (sub-second precision intact)."""
    from datetime import UTC, timedelta
    _login(client, monkeypatch)
    # Create a trade and capture its exact executed_at
    client.post("/trades", data={
        "ticker": "TZD", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": "", "tz_offset_minutes": "-480",
    })
    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    t = s.query(Trade).filter(Trade.ticker == "TZD").one()
    trade_id = t.id
    original_iso = t.executed_at.isoformat()
    original_ts = t.executed_at
    # What does the user see in the date input? The local-date of original.
    stored = t.executed_at if t.executed_at.tzinfo else t.executed_at.replace(tzinfo=UTC)
    local_dt = stored - timedelta(minutes=-480)
    same_local_date = local_dt.date().isoformat()
    # PUT with same date, just changing notes
    res = client.put(f"/trades/{trade_id}", data={
        "ticker": "TZD", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": same_local_date, "tz_offset_minutes": "-480",
        "original_executed_at_iso": original_iso, "notes": "edited",
    })
    assert res.status_code == 200
    s2 = next(db_base.session_scope())
    t2 = s2.query(Trade).filter(Trade.id == trade_id).one()
    assert t2.notes == "edited"
    # Timestamp must be EXACTLY the same — sub-second precision preserved.
    # Account for the possibility that the DB returns naive UTC datetimes
    # (depends on the column type). Normalize both sides for the compare.
    def _to_aware(d):
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    assert _to_aware(t2.executed_at) == _to_aware(original_ts)


def test_trades_update_recomputes_when_date_changed(client: TestClient, monkeypatch):
    """PUT /trades/{id} with a NEW date + original_executed_at_iso provided:
    helper sees mismatch → falls through to TZ-combine path. Stored date
    (in user-local TZ) matches the new submitted date."""
    from datetime import UTC, timedelta
    _login(client, monkeypatch)
    client.post("/trades", data={
        "ticker": "TZE", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": "", "tz_offset_minutes": "-480",
    })
    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    t = s.query(Trade).filter(Trade.ticker == "TZE").one()
    trade_id = t.id
    original_iso = t.executed_at.isoformat()
    new_date = "2026-04-01"  # different from the original (which is "now")
    res = client.put(f"/trades/{trade_id}", data={
        "ticker": "TZE", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": new_date, "tz_offset_minutes": "-480",
        "original_executed_at_iso": original_iso, "notes": "moved",
    })
    assert res.status_code == 200
    s2 = next(db_base.session_scope())
    t2 = s2.query(Trade).filter(Trade.id == trade_id).one()
    stored = t2.executed_at if t2.executed_at.tzinfo else t2.executed_at.replace(tzinfo=UTC)
    local_dt = stored - timedelta(minutes=-480)
    assert local_dt.date().isoformat() == new_date
```

- [ ] **Step 1b: Run, confirm 5 fail.**

```
uv run pytest tests/web/test_trades.py -k "tz_offset or update_preserves or update_recomputes" -v
```

- [ ] **Step 1c: Implement the helper and update both routes**

In `marketpulse/web/routes/trades.py`:

1. Verify imports include `date` and `timedelta` (existing imports already have `UTC, datetime, time`). Update the imports line:

```python
from datetime import UTC, date, datetime, time, timedelta
```

2. Add the helper just before `trades_page`:

```python
def _parse_executed_at(
    executed_at: str,
    tz_offset_minutes: int,
    original_iso: str = "",
) -> datetime:
    """Resolve form `executed_at` to a UTC datetime.

    Priority:
    1. Preserve-original: if `original_iso` is provided AND its user-local
       date (per tz_offset_minutes) equals the form's YYYY-MM-DD string,
       the trade is being edited without a date change — return the
       original full timestamp byte-for-byte. Sub-second precision intact.
    2. Blank → datetime.now(UTC).
    3. YYYY-MM-DD → combine with user's current local clock time → UTC.
       Provides sub-day ordering for sequentially-entered trades.
    4. Full ISO 8601 → parse as-is; naive treated as UTC.

    `tz_offset_minutes` follows JS Date.getTimezoneOffset() convention:
    Beijing (UTC+8) → -480. Formula: utc_naive = local_naive + offset.
    """
    s = executed_at.strip()
    orig = original_iso.strip()

    # Priority 1: preserve-original
    if orig:
        try:
            orig_dt = datetime.fromisoformat(orig.replace("Z", "+00:00"))
            if orig_dt.tzinfo is None:
                orig_dt = orig_dt.replace(tzinfo=UTC)
            orig_local = orig_dt + timedelta(minutes=-tz_offset_minutes)
            if s and len(s) == 10 and orig_local.date().isoformat() == s:
                return orig_dt
        except ValueError:
            pass  # fall through

    # Priority 2: blank
    if not s:
        return datetime.now(UTC)

    try:
        # Priority 3: YYYY-MM-DD
        if len(s) == 10:
            local_date = date.fromisoformat(s)
            now_utc_naive = datetime.now(UTC).replace(tzinfo=None)
            now_local_naive = now_utc_naive + timedelta(minutes=-tz_offset_minutes)
            local_dt_naive = datetime.combine(local_date, now_local_naive.time())
            return (
                local_dt_naive + timedelta(minutes=tz_offset_minutes)
            ).replace(tzinfo=UTC)
        # Priority 4: full ISO 8601
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"invalid executed_at: {exc}",
        ) from exc
```

3. `trades_add` — add `tz_offset_minutes: int = Form(0)` to parameter list (right after `executed_at`). Replace the entire date-parsing block with:

```python
    executed_at_dt = _parse_executed_at(executed_at, tz_offset_minutes)
```

4. `trades_update` — add both new params and pass `original_executed_at_iso`:

```python
    executed_at: str = Form(""),
    tz_offset_minutes: int = Form(0),
    original_executed_at_iso: str = Form(""),
    db: Session = Depends(get_db),
```

Replace its date-parsing block with:

```python
    executed_at_dt = _parse_executed_at(
        executed_at, tz_offset_minutes, original_executed_at_iso,
    )
```

- [ ] **Step 1d: Verify**

```
uv run pytest tests/web/test_trades.py -k "tz_offset or update_preserves or update_recomputes" -v
uv run pytest tests/web/test_trades.py 2>&1 | tail -5
uv run pytest 2>&1 | tail -3
uv run ruff check 2>&1 | tail -3
```
Expected: 5 new tests PASS, all file green, project green, ruff clean.

- [ ] **Step 1e: Commit**

```
git add marketpulse/web/routes/trades.py tests/web/test_trades.py
git commit -m "$(cat <<'EOF'
feat(trades): TZ-aware date parsing + preserve original on date-unchanged edits

New shared helper `_parse_executed_at(executed_at, tz_offset_minutes,
original_iso)` resolves form data to UTC with three priority branches:

1. Preserve-original: if the request supplies original_executed_at_iso
   AND its user-local date matches the form's YYYY-MM-DD, return the
   original timestamp byte-for-byte. This means editing a trade without
   changing the date keeps sub-second precision — critical for notes-
   only edits, which previously would silently shift the timestamp to
   "now".

2. Blank → datetime.now(UTC) (unchanged).

3. YYYY-MM-DD → combine the chosen date with the user's current local
   clock time (derived from tz_offset_minutes), convert to UTC. Keeps
   the user's chosen date intact when displayed in their TZ, and
   provides sub-day ordering for sequentially-entered trades.

4. Full ISO 8601 → parse as-is (unchanged).

Both POST /trades and PUT /trades/{id} now accept tz_offset_minutes;
PUT additionally accepts original_executed_at_iso for the preserve
path. Defaults make JS-less requests behave identically to before.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 2: Frontend hidden inputs + edit-mode plumbing

**Files:** `marketpulse/web/templates/trades.html`, `marketpulse/web/templates/partials/trades_table.html`, `tests/web/test_trades.py`

- [ ] **Step 2a: Write failing tests**

Append to `tests/web/test_trades.py`:

```python
def test_trades_form_has_tz_and_original_iso_inputs(client: TestClient, monkeypatch):
    """The /trades page must include hidden tz_offset_minutes and
    original_executed_at_iso inputs, plus JS that populates tz_offset on load."""
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert r.status_code == 200
    body = r.text
    import re
    tz_m = re.search(r'<input[^>]*name="tz_offset_minutes"[^>]*>', body)
    assert tz_m is not None and 'type="hidden"' in tz_m.group(0), (
        "hidden tz_offset_minutes input missing"
    )
    assert 'id="tz-offset-input"' in tz_m.group(0)
    orig_m = re.search(r'<input[^>]*name="original_executed_at_iso"[^>]*>', body)
    assert orig_m is not None and 'type="hidden"' in orig_m.group(0), (
        "hidden original_executed_at_iso input missing"
    )
    assert 'id="original-executed-at-iso"' in orig_m.group(0)
    assert "getTimezoneOffset" in body, "JS must populate tz_offset on load"
```

- [ ] **Step 2b: Run, confirm fails**

```
uv run pytest tests/web/test_trades.py::test_trades_form_has_tz_and_original_iso_inputs -v
```

- [ ] **Step 2c: Add the two hidden inputs to `trades.html`**

Find the existing line:

```html
    <input type="hidden" name="trade_id" id="trade-id-input" value="" />
```

Immediately after it, add:

```html
    <input type="hidden" name="tz_offset_minutes" id="tz-offset-input" value="0" />
    <input type="hidden" name="original_executed_at_iso" id="original-executed-at-iso" value="" />
```

- [ ] **Step 2d: Update `loadTradeIntoForm` and `exitEditMode` in `trades.html`**

Find `loadTradeIntoForm` in the script block. Replace its `executed_at` prefill line:

```javascript
      form.querySelector('[name="executed_at"]').value = data.executed_at || '';
```

with:

```javascript
      form.querySelector('[name="executed_at"]').value = data.executed_at_date || '';
      document.getElementById('original-executed-at-iso').value = data.executed_at_iso || '';
```

In `exitEditMode`, add a line to clear the original-iso input. After the existing `document.getElementById('trade-id-input').value = '';` line, insert:

```javascript
      document.getElementById('original-executed-at-iso').value = '';
```

- [ ] **Step 2e: Add tz_offset populate JS to `trades.html`**

At the end of the existing script block (just before `</script>`), add:

```javascript
    // Populate tz_offset on load so the backend can interpret YYYY-MM-DD
    // dates in the user's local TZ. Beijing (UTC+8) → -480.
    document.getElementById('tz-offset-input').value =
      new Date().getTimezoneOffset();
```

- [ ] **Step 2f: Update the Edit button payload in `partials/trades_table.html`**

Find the Edit button onclick line (added in the prior PR #21 commit). It currently passes `"executed_at": ...`. Replace that entire button block with:

```html
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
                "executed_at_date": (t.executed_at.strftime("%Y-%m-%d") if t.executed_at else ""),
                "executed_at_iso": (t.executed_at.isoformat() if t.executed_at else "")
              } | tojson }})'
              class="text-blue-600 text-xs hover:underline mr-2">编辑</button>
```

(Only the inner key list changed: `executed_at` → `executed_at_date` + new `executed_at_iso`.)

- [ ] **Step 2g: Verify**

```
uv run pytest tests/web/test_trades.py::test_trades_form_has_tz_and_original_iso_inputs -v
uv run pytest tests/web/test_trades.py 2>&1 | tail -5
uv run pytest 2>&1 | tail -3
uv run ruff check 2>&1 | tail -3
```
Expected: target test PASS, all green.

- [ ] **Step 2h: Commit**

```
git add marketpulse/web/templates/trades.html marketpulse/web/templates/partials/trades_table.html tests/web/test_trades.py
git commit -m "$(cat <<'EOF'
feat(trades): JS captures TZ offset + original ISO timestamp for edits

Two new hidden form inputs:
- tz_offset_minutes (always present): JS sets it on page load via
  new Date().getTimezoneOffset(). Beijing → -480.
- original_executed_at_iso: populated by loadTradeIntoForm when entering
  edit mode, cleared by exitEditMode. Carries the full ISO timestamp of
  the trade being edited so the backend can preserve it if the user
  doesn't change the date.

The Edit button in the trades table now passes BOTH the formatted date
(executed_at_date, for the visible <input type=date>) and the full ISO
(executed_at_iso, for the hidden input). loadTradeIntoForm fills both.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 3: Display times in user's local TZ

**Files:** `marketpulse/web/templates/partials/trades_table.html`, `marketpulse/web/templates/trades.html`, `tests/web/test_trades.py`

- [ ] **Step 3a: Write failing test**

Append to `tests/web/test_trades.py`:

```python
def test_trades_table_renders_time_with_data_utc(client: TestClient, monkeypatch):
    """Trade rows must wrap the time cell in <time data-utc=...> so JS can
    convert to user-local TZ on the client side."""
    _login(client, monkeypatch)
    client.post("/trades", data={
        "ticker": "TZF", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": "", "tz_offset_minutes": "-480",
    })
    r = client.get("/trades")
    assert r.status_code == 200
    body = r.text
    assert "<time data-utc=" in body, (
        "trade time cells must be wrapped in <time data-utc=...>"
    )
    # JS must include the conversion function.
    assert "applyLocalTime" in body, (
        "trades.html must include applyLocalTime() to convert times"
    )
    # Must hook htmx:afterSwap so the new rows get converted too.
    assert "htmx:afterSwap" in body, (
        "trades.html must re-apply local time after HTMX swaps the table"
    )
```

- [ ] **Step 3b: Run, confirm fails**

```
uv run pytest tests/web/test_trades.py::test_trades_table_renders_time_with_data_utc -v
```

- [ ] **Step 3c: Wrap the trade time cell in `<time data-utc>`**

In `marketpulse/web/templates/partials/trades_table.html`, find:

```html
          <td class="px-2 py-1 text-slate-500 text-xs">
            {{ (t.executed_at or t.created_at).strftime("%Y-%m-%d %H:%M") }}
          </td>
```

Replace with:

```html
          <td class="px-2 py-1 text-slate-500 text-xs">
            <time data-utc="{{ (t.executed_at or t.created_at).isoformat() }}">{{ (t.executed_at or t.created_at).strftime("%Y-%m-%d %H:%M") }}</time>
          </td>
```

The text inside `<time>` is the UTC fallback for JS-disabled clients. JS replaces it with the user-local equivalent.

Split rows (`s.ex_date.strftime("%Y-%m-%d")`) and dividend rows (same) are date-only and don't need this — leave them as plain text.

- [ ] **Step 3d: Add the JS converter to `trades.html`**

At the end of the existing script block (after the tz-offset populate line from Task 2), add:

```javascript
    // Convert all <time data-utc> elements to user-local format.
    // Runs once on load and again after every HTMX swap so newly-rendered
    // rows are converted too.
    function formatLocalTime(isoUtc) {
      const d = new Date(isoUtc);
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      const hh = String(d.getHours()).padStart(2, '0');
      const mm = String(d.getMinutes()).padStart(2, '0');
      return `${y}-${m}-${day} ${hh}:${mm}`;
    }
    function applyLocalTime(root) {
      (root || document).querySelectorAll('time[data-utc]').forEach(el => {
        el.textContent = formatLocalTime(el.dataset.utc);
      });
    }
    applyLocalTime();
    document.body.addEventListener('htmx:afterSwap', e => applyLocalTime(e.detail.target));
```

- [ ] **Step 3e: Verify**

```
uv run pytest tests/web/test_trades.py::test_trades_table_renders_time_with_data_utc -v
uv run pytest tests/web/test_trades.py 2>&1 | tail -5
uv run pytest 2>&1 | tail -3
uv run ruff check 2>&1 | tail -3
```
Expected: target test PASS, all green.

- [ ] **Step 3f: Commit**

```
git add marketpulse/web/templates/partials/trades_table.html marketpulse/web/templates/trades.html tests/web/test_trades.py
git commit -m "$(cat <<'EOF'
feat(trades): display trade times in user's local TZ via client-side conversion

Wraps the trade time cell in <time data-utc="ISO_TIMESTAMP">UTC_FALLBACK</time>.
A JS scanner (applyLocalTime) replaces the text content with the user-local
format on page load and after every HTMX swap (so newly-rendered rows after
add/edit are converted too).

JS-disabled clients see the UTC fallback — graceful degradation.

Split and dividend rows render ex-dates (date-only, no time) which look
identical in any TZ, so they aren't wrapped.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 4: Push and update PR

- [ ] **Step 4a: Push**

```
git push
```

- [ ] **Step 4b: Update PR description**

Edit PR #21 to mention the TZ work. Run:

```bash
gh pr edit 21 --body "$(cat <<'EOF'
## Summary

Bundled fixes and improvements for \`/trades\`:

1. **action drift after submit** — selecting 买入 sometimes recorded as 卖出.
   Root cause: \`form.reset()\` after a successful submit reset the visible select to 买入 but didn't fire its \`change\` event, so the hidden \`<input name=\"action\">\` kept its previous value. Fix: after-request hook now calls \`exitEditMode()\` which resets *and* re-syncs the hidden input.

2. **date field falsely required** — backend has accepted blank \`executed_at\` since the field was added, but the JS marked every \`.trade-field\` as required. Fix: \`data-optional=\"true\"\` on the date input; JS skips required for those.

3. **no edit support** — only Delete was available. New: \`PUT /trades/{id}\` endpoint (mirrors POST validation) + Edit button per row that prefills the form. \`recompute_ticker\` rebuilds Holding + realized_pl after every save. Ticker change on edit recomputes both old and new tickers.

4. **TZ-aware date storage** — when user fills only YYYY-MM-DD, backend used to store as midnight UTC. For non-UTC users (e.g., Beijing UTC+8) this caused date-boundary drift and lost sub-day ordering. Fix: hidden \`tz_offset_minutes\` input populated by JS from \`Date.getTimezoneOffset()\`. Backend combines the chosen date with user's *current* local clock time, then converts to UTC.

5. **edit preserves original timestamp** — editing a trade without changing the date now keeps the original \`executed_at\` byte-for-byte (sub-second precision intact). Notes-only edits, ticker-change edits, etc. don't silently shift the timestamp to \"now\". Implemented via a hidden \`original_executed_at_iso\` field carried through edit mode.

6. **display times in user's local TZ** — trade rows render \`<time data-utc=...>\` and JS converts to user-local on load and after every HTMX swap. UTC fallback for JS-disabled clients.

Specs:
- \`docs/superpowers/specs/2026-05-12-trades-page-fixes-design.md\` (bugs 1-3)
- \`docs/superpowers/specs/2026-05-12-trade-date-tz-aware-design.md\` (bugs 4-6)

## Test Plan

- [x] All tests pass (~310 total, including new TZ tests)
- [x] \`ruff check\` clean
- [ ] Manual after deploy:
  - [ ] Alternate 买入 / 卖出 across submits; verify each record matches the selected action
  - [ ] Submit a trade with the date field empty; verify the record gets today's date in your local TZ
  - [ ] Click 编辑 on a row, change ONLY the notes; verify the timestamp does NOT shift (look at \`Trade.executed_at\` in the table — should be identical)
  - [ ] Click 编辑, change the date; verify the timestamp updates to the new date at current time-of-day
  - [ ] Click 编辑 then 取消编辑; verify form clears and is back in add mode
  - [ ] In Beijing TZ, submit a trade with date \`2026-05-12\`; verify the displayed time shows 2026-05-12 (your local date), not the previous day
  - [ ] Submit two trades with the same date a few seconds apart; verify their stored timestamps differ (sub-day ordering)
  - [ ] Edit a trade and change its ticker; verify old ticker's holding goes away and new ticker's appears

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4c: Self-review**

Run and report:
- `git log --oneline | head -10`
- `uv run pytest 2>&1 | tail -3` — all green
- `uv run ruff check 2>&1 | tail -3` — clean
- `grep -c '_parse_executed_at' marketpulse/web/routes/trades.py` — at least 3 (definition + 2 calls)
- `grep -c original_executed_at_iso marketpulse/web/routes/trades.py` — at least 1
- `grep -c original-executed-at-iso marketpulse/web/templates/trades.html` — at least 1
- `grep -c data-utc marketpulse/web/templates/partials/trades_table.html` — at least 1
- `grep -c applyLocalTime marketpulse/web/templates/trades.html` — at least 2

Report PR URL: https://github.com/divxer/MarketPulse/pull/21
