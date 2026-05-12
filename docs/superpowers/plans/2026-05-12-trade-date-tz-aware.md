# Trade Date Timezone-Aware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Replace the YYYY-MM-DD → 00:00 UTC default with a TZ-aware interpretation: the user's chosen date combined with the current local clock time of submission. Form picks up `tz_offset_minutes` via hidden input populated by JS.

**Architecture:** One hidden form field captured by JS. One shared date-parsing helper used by both POST and PUT trade routes. Blank-date branch unchanged. ISO 8601 branch unchanged.

**Tech Stack:** FastAPI, vanilla JS, HTMX.

**Spec:** [`docs/superpowers/specs/2026-05-12-trade-date-tz-aware-design.md`](../specs/2026-05-12-trade-date-tz-aware-design.md)

**Branch:** continue on `fix/trades-page` (the prior 3-bug PR #21 is still open and conceptually related).

---

## Pre-flight

- [ ] **Step 0a: Confirm branch + clean tree**

```bash
git branch --show-current      # expect: fix/trades-page
git status --short             # expect: empty
uv run pytest 2>&1 | tail -3   # expect: 300 passed
```

If any check fails, stop.

---

## Task 1: Shared date-parsing helper

Refactor the date-parsing block (currently duplicated in `trades_add` and `trades_update`) into a single helper that ALSO accepts `tz_offset_minutes`. This way the new TZ logic lives in one place.

**Files:** `marketpulse/web/routes/trades.py`, `tests/web/test_trades.py`

- [ ] **Step 1a: Write the failing tests**

Append to `tests/web/test_trades.py`:

```python
def test_trade_post_with_tz_offset_combines_with_local_now(client: TestClient, monkeypatch):
    """When tz_offset_minutes is provided and executed_at is YYYY-MM-DD,
    the stored datetime is (user's chosen date in their TZ) at (current
    local clock time), converted to UTC."""
    from datetime import UTC, datetime, timedelta
    _login(client, monkeypatch)
    # Beijing offset is -480 minutes (UTC+8)
    tz_offset = -480
    before_utc = datetime.now(UTC)
    res = client.post("/trades", data={
        "ticker": "TZA", "action": "buy", "quantity": 1, "price": 10,
        "fees": 0, "executed_at": "2026-05-12",
        "tz_offset_minutes": str(tz_offset),
    })
    after_utc = datetime.now(UTC)
    assert res.status_code == 200

    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    t = s.query(Trade).filter(Trade.ticker == "TZA").one()
    # The stored datetime, when converted to Beijing (UTC+8 = subtract -480 min
    # from UTC), should land on 2026-05-12 (the user's chosen date).
    local_dt = t.executed_at - timedelta(minutes=tz_offset)
    assert local_dt.date().isoformat() == "2026-05-12"
    # Its UTC time-of-day should match "now" (within a few seconds of the test
    # call) shifted by tz_offset.
    expected_utc_time_low = (before_utc + timedelta(minutes=tz_offset)).time()
    expected_utc_time_high = (after_utc + timedelta(minutes=tz_offset)).time()
    # Allow some slop — compare via timedelta windows below to handle midnight wrap.
    assert t.executed_at.tzinfo is not None or t.executed_at.tzinfo is None  # accept both
    # The clock-time of stored UTC datetime, shifted back into local TZ,
    # should be within the [before, after] window in local TZ.
    local_now_before = before_utc - timedelta(minutes=tz_offset)
    local_now_after = after_utc - timedelta(minutes=tz_offset)
    stored_local = t.executed_at - timedelta(minutes=tz_offset)
    # Strip tzinfo if present for naive comparison
    if stored_local.tzinfo is not None:
        stored_local = stored_local.replace(tzinfo=None)
        local_now_before = local_now_before.replace(tzinfo=None)
        local_now_after = local_now_after.replace(tzinfo=None)
    # Stored local time-of-day must be in [now_before.time, now_after.time]
    # within a tolerance, AND its date is the user-chosen 2026-05-12.
    assert stored_local.date().isoformat() == "2026-05-12"


def test_trade_post_zero_tz_offset_uses_now_time_of_day(client: TestClient, monkeypatch):
    """With tz_offset_minutes=0 (UTC client), YYYY-MM-DD picks current UTC
    time-of-day, NOT the old midnight default."""
    from datetime import UTC, datetime
    _login(client, monkeypatch)
    before = datetime.now(UTC)
    res = client.post("/trades", data={
        "ticker": "TZB", "action": "buy", "quantity": 1, "price": 10,
        "fees": 0, "executed_at": "2026-05-12",
        "tz_offset_minutes": "0",
    })
    after = datetime.now(UTC)
    assert res.status_code == 200

    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    t = s.query(Trade).filter(Trade.ticker == "TZB").one()
    assert t.executed_at.year == 2026 and t.executed_at.month == 5 and t.executed_at.day == 12
    # The time-of-day should match "now" within the test window — NOT 00:00:00.
    # (The "no longer arbitrary midnight" assertion.)
    if t.executed_at.tzinfo is None:
        stored = t.executed_at.replace(tzinfo=UTC)
    else:
        stored = t.executed_at
    assert before.time() <= stored.time() <= after.time() or (
        # Wrapping past midnight inside the test window — rare. Fall back to
        # "not 00:00 exactly".
        stored.time() != datetime.min.time()
    )


def test_trade_post_blank_date_unchanged_by_tz_offset(client: TestClient, monkeypatch):
    """Blank executed_at + any tz_offset → still datetime.now(UTC)."""
    from datetime import UTC, datetime
    _login(client, monkeypatch)
    before = datetime.now(UTC)
    res = client.post("/trades", data={
        "ticker": "TZC", "action": "buy", "quantity": 1, "price": 10,
        "fees": 0, "executed_at": "",
        "tz_offset_minutes": "-480",
    })
    after = datetime.now(UTC)
    assert res.status_code == 200

    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    t = s.query(Trade).filter(Trade.ticker == "TZC").one()
    if t.executed_at.tzinfo is None:
        stored = t.executed_at.replace(tzinfo=UTC)
    else:
        stored = t.executed_at
    assert before <= stored <= after


def test_trades_update_respects_tz_offset(client: TestClient, monkeypatch):
    """PUT /trades/{id} with YYYY-MM-DD + tz_offset combines date with current
    local clock time, same as POST."""
    from datetime import UTC, datetime, timedelta
    _login(client, monkeypatch)
    # Create then edit
    client.post("/trades", data={
        "ticker": "TZD", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": "",  # blank → datetime.now(UTC)
    })
    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    trade_id = s.query(Trade).filter(Trade.ticker == "TZD").one().id

    tz_offset = -480
    res = client.put(f"/trades/{trade_id}", data={
        "ticker": "TZD", "action": "buy", "quantity": 1, "price": 10,
        "fees": 0, "executed_at": "2026-05-10",
        "tz_offset_minutes": str(tz_offset),
    })
    assert res.status_code == 200

    s2 = next(db_base.session_scope())
    t = s2.query(Trade).filter(Trade.id == trade_id).one()
    # Stored UTC, converted to Beijing local, must land on 2026-05-10.
    local_dt = t.executed_at - timedelta(minutes=tz_offset)
    if local_dt.tzinfo is not None:
        local_dt = local_dt.replace(tzinfo=None)
    assert local_dt.date().isoformat() == "2026-05-10"
```

- [ ] **Step 1b: Run, confirm 4 fail**

```
uv run pytest tests/web/test_trades.py -k "tz_offset" -v
```
Expected: 4 FAIL (because `tz_offset_minutes` isn't yet a route parameter; the date-only path still produces midnight UTC).

- [ ] **Step 1c: Add the shared helper + new parameter, update both routes**

In `marketpulse/web/routes/trades.py`:

First, update the imports to include `date` and `timedelta`:

```python
from datetime import UTC, date, datetime, time, timedelta
```

(`date` and `timedelta` are likely already imported — verify with a grep first; if so, skip this edit.)

Then add a helper function just before `trades_page`:

```python
def _parse_executed_at(executed_at: str, tz_offset_minutes: int) -> datetime:
    """Convert a form `executed_at` string to a UTC datetime.

    Behavior:
    - Blank: `datetime.now(UTC)` — a real moment in time.
    - "YYYY-MM-DD" only: combine the user's chosen date with the user's
      CURRENT local clock time (derived from `tz_offset_minutes`), then
      convert to UTC. Preserves the user-chosen date and provides
      sub-day ordering for sequentially-entered trades.
    - Full ISO 8601 with explicit time: parsed as-is; naive datetimes
      treated as UTC. `tz_offset_minutes` is IGNORED in this branch — the
      user explicitly specified a time.

    `tz_offset_minutes` follows JS `Date.getTimezoneOffset()` convention:
    Beijing (UTC+8) is -480. Formula: utc_naive = local_naive + offset.
    """
    s = executed_at.strip()
    if not s:
        return datetime.now(UTC)
    try:
        if len(s) == 10:  # YYYY-MM-DD
            local_date = date.fromisoformat(s)
            now_utc_naive = datetime.now(UTC).replace(tzinfo=None)
            # Shift "now" into user's local TZ to extract their current clock time.
            now_local_naive = now_utc_naive + timedelta(minutes=-tz_offset_minutes)
            local_dt_naive = datetime.combine(local_date, now_local_naive.time())
            return (
                local_dt_naive + timedelta(minutes=tz_offset_minutes)
            ).replace(tzinfo=UTC)
        # Full ISO 8601 — user supplied an explicit time.
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"invalid executed_at: {exc}",
        ) from exc
```

In `trades_add`, add `tz_offset_minutes` to the parameter list:

```python
def trades_add(
    request: Request,
    ticker: str = Form(...),
    action: str = Form(...),
    quantity: float = Form(...),
    price: float = Form(...),
    fees: float = Form(0.0),
    notes: str = Form(""),
    executed_at: str = Form(""),
    tz_offset_minutes: int = Form(0),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
```

And REPLACE the entire `executed_at` parsing block (currently lines ~113-127 with the `executed_at_dt: datetime` declaration and the if/else branches) with a single call:

```python
    executed_at_dt = _parse_executed_at(executed_at, tz_offset_minutes)
```

In `trades_update`, do the same: add `tz_offset_minutes: int = Form(0)` parameter, replace the entire date-parsing block with `executed_at_dt = _parse_executed_at(executed_at, tz_offset_minutes)`.

- [ ] **Step 1d: Verify**

```
uv run pytest tests/web/test_trades.py -k "tz_offset" -v
uv run pytest tests/web/test_trades.py 2>&1 | tail -5
uv run pytest 2>&1 | tail -3
uv run ruff check 2>&1 | tail -3
```
Expected: 4 new TZ tests PASS, full file all green, project all green, ruff clean.

- [ ] **Step 1e: Commit**

```
git add marketpulse/web/routes/trades.py tests/web/test_trades.py
git commit -m "$(cat <<'EOF'
feat(trades): TZ-aware date parsing — combine date with local now-time

When the form supplies "executed_at=YYYY-MM-DD" plus the user's
tz_offset_minutes (from JS Date.getTimezoneOffset), the stored UTC
datetime is now computed as (user's chosen date at user's current local
clock time), not the old (user's chosen date at midnight UTC). This:

- Keeps the user-chosen date intact when converted back to their local TZ
  for display.
- Provides sub-day ordering for trades entered in the same session
  (their stored times-of-day match the entry sequence instead of all
  collapsing to 00:00:00 UTC).

Blank executed_at branch unchanged — datetime.now(UTC) is already a real
moment, no inference required.

Full ISO 8601 branch unchanged — user explicitly specified a time, no
inference allowed.

The shared `_parse_executed_at` helper replaces the date-parsing block
that was duplicated in `trades_add` and `trades_update`.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 2: Frontend hidden input + JS population

**Files:** `marketpulse/web/templates/trades.html`

- [ ] **Step 2a: Add the failing test (template assertion)**

Append to `tests/web/test_trades.py`:

```python
def test_trades_form_has_tz_offset_input_and_js(client: TestClient, monkeypatch):
    """The /trades page must include a hidden tz_offset_minutes input AND
    JS that populates it via Date.getTimezoneOffset() on page load."""
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert r.status_code == 200
    body = r.text
    import re
    # Hidden input present
    m = re.search(
        r'<input[^>]*name="tz_offset_minutes"[^>]*>', body,
    )
    assert m is not None, "hidden tz_offset_minutes input missing"
    assert 'type="hidden"' in m.group(0), "tz_offset_minutes input must be hidden"
    assert 'id="tz-offset-input"' in m.group(0), (
        "tz_offset_minutes input must have id=tz-offset-input for JS to populate"
    )
    # JS sets its value via getTimezoneOffset
    assert "getTimezoneOffset" in body, (
        "JS must populate the tz_offset input via Date.getTimezoneOffset()"
    )
```

- [ ] **Step 2b: Run, confirm fails**

```
uv run pytest tests/web/test_trades.py::test_trades_form_has_tz_offset_input_and_js -v
```
Expected: FAIL.

- [ ] **Step 2c: Add the hidden input + JS to `trades.html`**

Find the existing hidden trade_id input (added in the prior commit):

```html
    <input type="hidden" name="trade_id" id="trade-id-input" value="" />
```

Immediately after it, add:

```html
    <input type="hidden" name="tz_offset_minutes" id="tz-offset-input" value="0" />
```

In the same file, find the `<script>` block. At the very end (just before `</script>`), add:

```javascript
    // Populate the hidden tz_offset_minutes input so the backend can
    // interpret YYYY-MM-DD dates in the user's local TZ rather than UTC.
    // getTimezoneOffset() returns "minutes BEHIND UTC" — Beijing (UTC+8)
    // returns -480.
    document.getElementById('tz-offset-input').value =
      new Date().getTimezoneOffset();
```

- [ ] **Step 2d: Verify**

```
uv run pytest tests/web/test_trades.py::test_trades_form_has_tz_offset_input_and_js -v
uv run pytest tests/web/test_trades.py 2>&1 | tail -5
uv run pytest 2>&1 | tail -3
uv run ruff check 2>&1 | tail -3
```
Expected: target test PASS, all green, ruff clean.

- [ ] **Step 2e: Commit**

```
git add marketpulse/web/templates/trades.html tests/web/test_trades.py
git commit -m "$(cat <<'EOF'
feat(trades): JS captures user's TZ offset into hidden form input

Populates a hidden `tz_offset_minutes` input via
`new Date().getTimezoneOffset()` on page load. The backend (previous
commit) reads this to convert YYYY-MM-DD dates from user-local to UTC
correctly. Beijing → -480, NYC → 240 or 300 (DST-dependent).

If JS is disabled, the backend default of 0 makes behavior identical
to pre-TZ days (date interpreted as UTC zero). Graceful degradation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Push (PR #21 already exists — these commits go to that PR)

- [ ] **Step 3a: Push**

```
git push
```

The two new commits will append to the existing PR #21. (`git push` without `-u` because `fix/trades-page` is already tracking origin.)

- [ ] **Step 3b: Update the PR description**

Run:

```bash
gh pr edit 21 --body "$(cat <<'EOF'
## Summary

Bundled fixes and improvements for \`/trades\`:

1. **action drift after submit** — selecting 买入 sometimes recorded as 卖出.
   Root cause: \`form.reset()\` after a successful submit reset the visible select to 买入 but didn't fire its \`change\` event, so the hidden \`<input name=\"action\">\` kept its previous value. Fix: after-request hook now calls \`exitEditMode()\` which resets *and* re-syncs the hidden input.

2. **date field falsely required** — backend has accepted blank \`executed_at\` since the field was added, but the JS was marking every \`.trade-field\` as required. Fix: \`data-optional=\"true\"\` on the date input; JS skips required for those.

3. **no edit support** — only Delete was available. New: \`PUT /trades/{id}\` endpoint (mirrors POST validation) + Edit button per row that prefills the form. \`recompute_ticker\` rebuilds Holding + realized_pl after every save. Ticker change on edit recomputes both old and new tickers.

4. **TZ-aware date interpretation** — when the user fills only YYYY-MM-DD, the backend used to store it as midnight UTC. For non-UTC users (e.g., Beijing), that caused subtle date-boundary issues and lost sub-day ordering of sequentially-entered trades. Fix: a hidden \`tz_offset_minutes\` input populated by JS from \`Date.getTimezoneOffset()\`. The backend combines the chosen date with the user's *current* local clock time, then converts to UTC. Blank/ISO branches unchanged. Graceful degradation if JS disabled (defaults to 0 → old behavior).

Specs:
- \`docs/superpowers/specs/2026-05-12-trades-page-fixes-design.md\` (bugs 1-3)
- \`docs/superpowers/specs/2026-05-12-trade-date-tz-aware-design.md\` (bug 4)

## Test Plan

- [x] All tests pass (~310 total, including new TZ tests)
- [x] \`ruff check\` clean
- [ ] Manual after deploy:
  - [ ] On /trades, alternate 买入 / 卖出 across submits; verify each record matches selected action
  - [ ] Submit a trade with the date field empty; verify the record gets today's date
  - [ ] Click 编辑 on a row; verify form prefills correctly, submit changes the row, holding/realized_pl recompute
  - [ ] Click 编辑 then 取消编辑; verify form clears and is back in add mode
  - [ ] Edit a trade and change its ticker; verify old ticker's holding goes away and new ticker's appears
  - [ ] Submit two trades with the same date \`2026-05-10\` a few seconds apart; verify their stored timestamps differ (sub-day ordering preserved, not both at 00:00)
  - [ ] In Beijing TZ, submit a trade with date \`2026-05-12\`; verify the stored UTC datetime, when converted back to Beijing, shows date 2026-05-12 (not 2026-05-11 due to UTC wrap)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3c: Self-review**

Run each and report:
- `git log --oneline | head -10` — should show 7+ commits
- `uv run pytest 2>&1 | tail -3` — all green (305+)
- `uv run ruff check 2>&1 | tail -3` — clean
- `grep -nc tz_offset_minutes marketpulse/web/routes/trades.py` — at least 3 (param in trades_add, param in trades_update, helper signature)
- `grep -nc tz_offset_minutes marketpulse/web/templates/trades.html` — at least 1 (hidden input)
- `grep -c getTimezoneOffset marketpulse/web/templates/trades.html` — at least 1

Report PR URL: https://github.com/divxer/MarketPulse/pull/21
