# Trade Date Timezone-Aware Storage + Display — Design Spec

**Status:** Approved (revised after Update-preservation + Display-TZ concerns)
**Author:** harvey
**Date:** 2026-05-12

## Goals

Three coupled improvements to `/trades`:

1. **TZ-aware date input**: when user fills `YYYY-MM-DD`, combine with current local clock time → UTC storage. Preserves chosen date when shown in user's TZ.
2. **Edit preserves original time**: editing a trade without changing the date keeps the original timestamp byte-for-byte. Only changing the date re-derives the timestamp.
3. **Display in user's TZ**: timestamps render in the user's local timezone (currently they're raw UTC).

## Why

Three real bugs the user flagged:

- Beijing user enters `2026-05-12` → stored as midnight UTC → equivalent to `2026-05-12 08:00 Beijing`. Adjacent dates can wrap across the UTC day boundary in some hours. Multiple same-day trades all collapse to one instant — no entry order.
- An edit that touches only `notes` would re-derive `executed_at` to "now" because the form's date is YYYY-MM-DD only and the parser combines it with current clock time. Silent data loss.
- The table renders UTC times in a Chinese-locale UI. A trade entered at 14:30 Beijing displays as 06:30 — confusing.

## Architecture

### Storage layer — unchanged

Continue storing `Trade.executed_at` as UTC `datetime`. No schema change.

### Form (per-submit metadata)

Two new hidden form fields:

- `tz_offset_minutes` (always present) — populated by JS via `Date.getTimezoneOffset()` on page load. Beijing (UTC+8) returns `-480`. Convention used here: `utc_naive = local_naive + timedelta(minutes=tz_offset_minutes)`.
- `original_executed_at_iso` (populated only when entering edit mode) — full ISO datetime of the trade being edited. Empty for new-trade submissions.

### Backend date parser

A single shared helper:

```python
def _parse_executed_at(
    executed_at: str,
    tz_offset_minutes: int,
    original_iso: str = "",
) -> datetime:
    """Resolve form fields to a UTC datetime.

    Priority:
    1. If `original_iso` is provided AND its date matches the form's date
       string, the trade is being edited without a date change — return
       the original full timestamp (preserves sub-second precision).
    2. Otherwise: blank → datetime.now(UTC).
    3. Otherwise YYYY-MM-DD → combine with current local clock time → UTC.
    4. Otherwise full ISO 8601 → parsed as-is.
    """
```

Used by both `trades_add` (where `original_iso` is always blank → behaves as a new trade) and `trades_update` (where `original_iso` is the existing trade's `executed_at.isoformat()`).

### Display layer — frontend conversion

Replace the current Jinja `{{ t.executed_at.strftime("%Y-%m-%d %H:%M") }}` rendering with:

```html
<time data-utc="{{ t.executed_at.isoformat() }}">{{ t.executed_at.strftime("%Y-%m-%d %H:%M") }}</time>
```

The text fallback (UTC strftime) is what the user sees if JS is disabled. JS on load converts each `<time data-utc>` element to user-local:

```javascript
function formatLocalTime(isoUtc) {
  const d = new Date(isoUtc);
  // YYYY-MM-DD HH:MM — match the existing format, just in local TZ.
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${y}-${m}-${day} ${hh}:${mm}`;
}
document.querySelectorAll('time[data-utc]').forEach(el => {
  el.textContent = formatLocalTime(el.dataset.utc);
});
```

Two scenarios:
- Initial page load: scanner runs once, all rows converted.
- After HTMX swap (POST/PUT replaces the table partial): the scanner needs to re-run. HTMX exposes a global event `htmx:afterSwap` we hook into.

## Components

### Frontend

**`marketpulse/web/templates/trades.html`:**

Inside the form, add two new hidden inputs:

```html
<input type="hidden" name="tz_offset_minutes" id="tz-offset-input" value="0" />
<input type="hidden" name="original_executed_at_iso" id="original-executed-at-iso" value="" />
```

Add JS at the end of the existing script block:

```javascript
// Set TZ offset on load (and re-set if user changes system TZ — rare).
document.getElementById('tz-offset-input').value =
  new Date().getTimezoneOffset();

// Convert all <time data-utc> elements to user-local on load + after every HTMX swap.
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

Update `loadTradeIntoForm(data)` to populate `original_executed_at_iso`. Update `exitEditMode()` to clear it. The button's onclick now needs to pass the full ISO timestamp, not just the date:

```javascript
function loadTradeIntoForm(data) {
  // ... existing prefills ...
  form.querySelector('[name="executed_at"]').value = data.executed_at_date || '';
  document.getElementById('original-executed-at-iso').value =
    data.executed_at_iso || '';
  // ... rest unchanged ...
}

function exitEditMode() {
  // ... existing resets ...
  document.getElementById('original-executed-at-iso').value = '';
  // ... rest unchanged ...
}
```

**`marketpulse/web/templates/partials/trades_table.html`:**

Edit button onclick payload now includes BOTH the date (for the visible input) AND the full ISO (for the hidden field). Also: wrap the displayed time cell in `<time data-utc>`. The current cells:

```html
{# trade row #}
<td class="px-2 py-1 text-slate-500 text-xs">
  {{ (t.executed_at or t.created_at).strftime("%Y-%m-%d %H:%M") }}
</td>
```

becomes:

```html
<td class="px-2 py-1 text-slate-500 text-xs">
  <time data-utc="{{ (t.executed_at or t.created_at).isoformat() }}">
    {{ (t.executed_at or t.created_at).strftime("%Y-%m-%d %H:%M") }}
  </time>
</td>
```

Split rows (`s.ex_date.strftime("%Y-%m-%d")`) and dividend rows (same) are DATE-ONLY (not datetime), so they don't need the time-element wrap — leaving them as plain text is fine. They render the same in any TZ.

Edit-button payload:

```html
<button type="button" onclick='loadTradeIntoForm({{ {
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

(Field name changed from `executed_at` → `executed_at_date` to match the new JS expecting two fields.)

### Backend (`marketpulse/web/routes/trades.py`)

`_parse_executed_at` body:

```python
def _parse_executed_at(
    executed_at: str,
    tz_offset_minutes: int,
    original_iso: str = "",
) -> datetime:
    s = executed_at.strip()
    orig = original_iso.strip()

    # Edit mode: if date is unchanged from the original, preserve the
    # original full timestamp byte-for-byte (sub-second precision intact).
    if orig:
        try:
            orig_dt = datetime.fromisoformat(orig.replace("Z", "+00:00"))
            if orig_dt.tzinfo is None:
                orig_dt = orig_dt.replace(tzinfo=UTC)
            # Compare on user-local date if tz_offset provided, else UTC date.
            orig_local = orig_dt + timedelta(minutes=-tz_offset_minutes)
            if s and len(s) == 10 and orig_local.date().isoformat() == s:
                return orig_dt
        except ValueError:
            # Bad original_iso — fall through to normal parsing.
            pass

    if not s:
        return datetime.now(UTC)
    try:
        if len(s) == 10:  # YYYY-MM-DD
            local_date = date.fromisoformat(s)
            now_utc_naive = datetime.now(UTC).replace(tzinfo=None)
            now_local_naive = now_utc_naive + timedelta(minutes=-tz_offset_minutes)
            local_dt_naive = datetime.combine(local_date, now_local_naive.time())
            return (
                local_dt_naive + timedelta(minutes=tz_offset_minutes)
            ).replace(tzinfo=UTC)
        # Full ISO 8601
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"invalid executed_at: {exc}",
        ) from exc
```

`trades_add` and `trades_update` both add `tz_offset_minutes: int = Form(0)`. `trades_update` ALSO adds `original_executed_at_iso: str = Form("")` and passes it as the third arg to the helper.

## Edge Cases

| Case | Behavior |
|---|---|
| Edit a trade, change only notes; date input is unchanged | `original_executed_at_iso` matches the date input → original timestamp preserved exactly. Sub-second precision intact. ✓ |
| Edit a trade, change the date to a new value | `original_executed_at_iso` provided but its date doesn't match the new date input → falls through to TZ-aware combine. Time-of-day becomes "now". |
| Edit a trade originally created on 2026-05-12 06:00 Beijing (UTC 22:00 May 11). User in Beijing TZ sees the date input filled with "2026-05-12". User submits unchanged. | `original_iso` = "2026-05-11T22:00..."; `orig_local` (Beijing) = 2026-05-12; matches form date "2026-05-12" → preserved. ✓ |
| JS disabled (no tz_offset, no original_iso submitted) | Backend defaults to 0 and "" → date interpreted as midnight UTC (old behavior). Display falls back to UTC strftime. Graceful degradation. |
| HTMX swaps the table after a POST | `htmx:afterSwap` listener re-runs `applyLocalTime(e.detail.target)`. New rows get converted. |
| User imports Robinhood CSV | CSV path passes full datetimes through `record_trade` directly, not the helper. Unchanged. Displayed via the same `<time data-utc>` mechanism. |

## Tests

`tests/web/test_trades.py` — new tests:

1. `test_trade_post_with_tz_offset_combines_with_local_now` (POST + YYYY-MM-DD + tz_offset)
2. `test_trade_post_zero_tz_offset_uses_now_time_of_day` (POST + UTC client)
3. `test_trade_post_blank_date_unchanged_by_tz_offset` (POST + blank date)
4. `test_trades_update_preserves_original_when_date_unchanged` (PUT, same date, different notes)
5. `test_trades_update_recomputes_when_date_changed` (PUT, new date, expect TZ combine)
6. `test_trades_form_has_tz_and_original_iso_inputs` (template includes the 2 hidden inputs and the JS)
7. `test_trades_table_renders_time_with_data_utc` (timeline rows include `<time data-utc>` wrapper)

## File Manifest

**Modified:**
- `marketpulse/web/routes/trades.py` — `_parse_executed_at` helper with original-preserving branch, new params on both routes
- `marketpulse/web/templates/trades.html` — 2 hidden inputs, JS to populate tz_offset, JS to convert displayed times, updated `loadTradeIntoForm` / `exitEditMode`
- `marketpulse/web/templates/partials/trades_table.html` — `<time data-utc>` wrap on trade time cells; edit button payload includes both `executed_at_date` and `executed_at_iso`
- `tests/web/test_trades.py` — 7 new tests

**Unchanged:**
- `marketpulse/holdings/trades.py`, `marketpulse/db/models.py` — no schema or business logic change
- Robinhood import path

## Risk

**Medium.** Three coupled changes (backend parser, form fields, display conversion). Each has graceful degradation if JS is disabled or its field is missing, so the worst case is "old midnight-UTC behavior" — not a regression worse than what existed before this PR.

The original-preservation branch is the highest-leverage correctness fix and has a clear test for it.

## Out of Scope

- Datetime picker UI for past trades with known specific time (user can still paste full ISO 8601 in the date input — backend honors it)
- Per-user persistent TZ preference (browser TZ is the source of truth)
- Splits / dividends display TZ (their dates are ex-date dates from external sources, not user-local)
- Day-precision vs UTC-time-zone-day ambiguity for the date input — accepted as "user always picks what they see"
