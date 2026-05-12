# Trade Date Timezone-Aware Storage — Design Spec

**Status:** Approved
**Author:** harvey
**Date:** 2026-05-12

## Goal

When a user fills a date (`YYYY-MM-DD`) for a past trade, store it as **(that date in user's local TZ) at (the current local clock time)** converted to UTC. Instead of the current behavior which stores it as `YYYY-MM-DD 00:00 UTC` — an arbitrary midnight that misaligns with the user's calendar in non-UTC time zones and provides no sub-day ordering for sequentially-entered trades.

## Why

Current behavior:

```python
# trades_add / trades_update (the YYYY-MM-DD branch):
executed_at_dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)
```

Problems for a Beijing-based user entering "2026-05-12":
- Stored as `2026-05-12 00:00 UTC` = `2026-05-12 08:00 Beijing` — close to correct, but adjacent dates (e.g., `2026-05-01`) cross the UTC day boundary in some hours.
- Multiple trades entered the same session all collapse to `00:00 UTC` — no entry-order preserved.
- Reverse direction: `datetime.now(UTC)` for "blank" gives `2026-05-12 16:00 UTC` = `2026-05-13 00:00 Beijing`, displaying as 2026-05-13 even though the user submitted "today". This already drifts and is not what the user wants.

Option C from prior discussion: pick up the user's TZ offset via JS, and:
- For **blank**: still `datetime.now(UTC)` — the moment is real, conversion-time display will show the user's local "now". *Note:* depending on how the display layer renders the timestamp, this may show tomorrow's date in non-UTC zones. Resolved separately by displaying in user's TZ — see "Display" below.
- For **YYYY-MM-DD only**: combine user's chosen date with the user's *current* clock time (the moment they submitted), interpret as user-local, convert to UTC. Preserves chosen date + entry-order.

## Architecture

One new form field (`tz_offset_minutes`), parsed by both `trades_add` and `trades_update` (and reused by future routes for splits/dividends if needed). The browser provides it via JS on every form submit.

`getTimezoneOffset()` returns "minutes that the local TZ is BEHIND UTC". E.g., Beijing (UTC+8) returns `-480`. To convert a local datetime to UTC, **subtract** the offset minutes (UTC = local - offset_minutes). To convert UTC to local, add the offset.

Wait — the standard JS convention: `getTimezoneOffset()` for UTC+8 returns -480, meaning "you need to ADD -480 minutes to local time to get UTC" → which means "subtract 8 hours from local to get UTC"... let me lock this down:

```python
# Convention used here: tz_offset_minutes = result of Date.getTimezoneOffset()
# For Beijing (UTC+8): tz_offset_minutes = -480
# Formula: utc_naive = local_naive + timedelta(minutes=tz_offset_minutes)
#   Beijing local 14:00 → 14:00 + (-480 min) = 06:00 UTC ✓
```

(Test in JS console: `new Date('2026-05-12 14:00').getTimezoneOffset()` in Beijing returns `-480`, and that 14:00 local is 06:00 UTC.)

## Components

### Frontend (`marketpulse/web/templates/trades.html`)

Add a hidden input inside the form:

```html
<input type="hidden" name="tz_offset_minutes" id="tz-offset-input" value="0" />
```

Add JS at the bottom of the existing script block to populate it on page load (and keep it fresh if the user changes TZ — rare, but harmless):

```javascript
document.getElementById('tz-offset-input').value = new Date().getTimezoneOffset();
```

### Backend (`marketpulse/web/routes/trades.py`)

Both `trades_add` and `trades_update` add:

```python
tz_offset_minutes: int = Form(0),
```

The date-parsing block changes from:

```python
if len(s) == 10:  # YYYY-MM-DD
    executed_at_dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)
```

to:

```python
if len(s) == 10:  # YYYY-MM-DD — combine with user's current local clock time
    local_date = date.fromisoformat(s)
    # Current UTC moment shifted into user's local TZ → naive local "now".
    now_utc = datetime.now(UTC)
    now_local_naive = now_utc.replace(tzinfo=None) + timedelta(
        minutes=-tz_offset_minutes,
    )
    # User chose `local_date` + we use the clock time of "now" in their TZ.
    local_dt_naive = datetime.combine(local_date, now_local_naive.time())
    # Convert back to UTC.
    executed_at_dt = (
        local_dt_naive + timedelta(minutes=tz_offset_minutes)
    ).replace(tzinfo=UTC)
```

Blank branch unchanged (`datetime.now(UTC)`).

Full-ISO-8601 branch unchanged (`fromisoformat`).

Robinhood-CSV import path unchanged (it provides full datetimes with timezones, no inference needed).

### Display

Out of scope. The display layer currently renders `t.executed_at.strftime("%Y-%m-%d %H:%M")` in UTC. After this change, sub-day timestamps will be more meaningful (real entry-order data), but they're still UTC. A separate fix could convert to user's TZ at display time using the same `tz_offset_minutes` field captured in a cookie or similar. **Deferred.**

## Edge Cases

| Case | Behavior |
|---|---|
| JS disabled or hidden field missing | `tz_offset_minutes: int = Form(0)` defaults to 0 → behavior identical to current (treat date as UTC zero). Graceful degradation. |
| User in UTC (`getTimezoneOffset() === 0`) | Combine with current UTC time-of-day → effectively `datetime.now(UTC).replace(year/month/day from picked date)`. Correct. |
| Past trade with a known specific time (the user types `2026-05-12T14:30:00` manually) | Falls through the full-ISO branch, unchanged. TZ offset ignored. |
| Robinhood CSV import | Unaffected — its dates come from the CSV with full timestamps. |
| Edit a trade and the user is in a different TZ than when they created it | The edit re-derives `executed_at` from the form's date + the user's *current* local time. If the user only wants to change one non-date field (e.g., notes), the form prefills the existing date — but `executed_at_dt` will be recomputed using current clock time, so the stored time-of-day will shift to "now". **Acceptable risk** — if precision matters, user can type a full ISO datetime manually. |

## Tests

`tests/web/test_trades.py` — new tests:

1. `test_trade_post_with_tz_offset_combines_with_local_now`: POST with `executed_at=YYYY-MM-DD` and `tz_offset_minutes=-480` (Beijing). Assert stored `executed_at` has the user's chosen date when converted to Beijing TZ, and its UTC time-of-day matches `(datetime.now(UTC) - timedelta(minutes=-480)).time()` ± 5 seconds (to allow for the test's own clock drift).

2. `test_trade_post_blank_tz_offset_defaults_to_utc_midnight`: POST with `executed_at=YYYY-MM-DD` and `tz_offset_minutes=0` (or omitted). Assert stored timestamp is `YYYY-MM-DD HH:MM:SS UTC` where HH:MM:SS is approximately current UTC time (not the old midnight default).

3. `test_trade_post_blank_date_unchanged`: POST with `executed_at=""` and `tz_offset_minutes=-480`. Assert `executed_at` is `datetime.now(UTC)` regardless of TZ — blank means "right now", which is a real moment, no inference needed.

4. `test_trades_update_respects_tz_offset`: same flow against PUT /trades/{id}.

## File Manifest

**Modified:**
- `marketpulse/web/templates/trades.html` — hidden input + 1 line JS
- `marketpulse/web/routes/trades.py` — `tz_offset_minutes` parameter + date-parsing block (twice: `trades_add` and `trades_update`). Refactor the date-parsing into a shared helper to avoid duplication.
- `tests/web/test_trades.py` — 4 new tests

**Unchanged:**
- `marketpulse/holdings/trades.py` — no change to the trade model or recompute logic
- `marketpulse/db/models.py` — no schema change
- CSV import path

## Risk

**Low.** Additive change — `tz_offset_minutes` defaults to 0, so JS-less clients behave exactly like today. Same-shape tests already cover blank/date/ISO branches. The new branch is one date-combine arithmetic operation.

## Out of Scope

- Display-side TZ conversion (defer)
- Datetime picker UI (defer)
- Per-user persistent TZ preference (defer)
- Splits / dividends with TZ-aware dates (their dates are already ex-dates from external sources)
