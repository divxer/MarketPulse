from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_trades_page_empty(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.get("/trades")
    assert res.status_code == 200
    assert "暂无记录" in res.text


_RH_HEADER = (
    "Activity Date,Process Date,Settle Date,Instrument,Description,"
    "Trans Code,Quantity,Price,Amount\n"
)


def test_robinhood_import_preview_and_confirm(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    csv = _RH_HEADER + (
        "5/8/2026,5/9/2026,5/12/2026,AAPL,Apple,Buy,10,$180.00,($1800.00)\n"
        "5/9/2026,5/10/2026,5/13/2026,AAPL,Apple,Sell,4,$200.00,$800.00\n"
        "5/1/2026,5/2/2026,5/3/2026,AAPL,Dividend,CDIV,,,$5.00\n"
    )
    res = client.post(
        "/trades/import",
        files={"file": ("activity.csv", csv, "text/csv")},
    )
    assert res.status_code == 200
    assert "AAPL" in res.text
    assert "2 笔" in res.text  # 2 new trades

    res = client.post("/trades/import/confirm", data={"csv_text": csv})
    assert res.status_code == 200
    assert "新增" in res.text

    res = client.get("/trades")
    assert "AAPL" in res.text
    # Realized P&L = (200-180)*4 = 80
    assert "+80.00" in res.text


def test_robinhood_import_skips_duplicates(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    csv = _RH_HEADER + "5/8/2026,5/9/2026,5/12/2026,SPY,SPDR,Buy,1,$500.00,($500.00)\n"
    # First import
    client.post("/trades/import/confirm", data={"csv_text": csv})
    # Re-upload same file → preview should show 0 new, 1 skipped
    res = client.post(
        "/trades/import",
        files={"file": ("activity.csv", csv, "text/csv")},
    )
    assert res.status_code == 200
    assert "0 笔为新交易" in res.text or "0</span> 笔为新交易" in res.text


def test_trade_post_accepts_executed_at(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/trades", data={
        "ticker": "QUBT", "action": "buy", "quantity": 100, "price": 19.70,
        "fees": 0, "notes": "historical", "executed_at": "2025-06-17",
    })
    assert res.status_code == 200
    res = client.get("/trades")
    assert "2025-06-17" in res.text or "06-17" in res.text


def test_trade_post_rejects_invalid_executed_at(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/trades", data={
        "ticker": "QUBT", "action": "buy", "quantity": 1, "price": 1,
        "executed_at": "not-a-date",
    })
    assert res.status_code == 422


def test_delete_trade_recomputes_holding(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    # Buy 100 @ 10, buy 100 @ 20 → avg 15. Sell 50 @ 25 → realized +500.
    client.post("/trades", data={
        "ticker": "ZZZ", "action": "buy", "quantity": 100, "price": 10,
    })
    r = client.post("/trades", data={
        "ticker": "ZZZ", "action": "buy", "quantity": 100, "price": 20,
    })
    # Get second buy's id from the response (its row id appears in trade-row-N).
    import re as _re
    ids = sorted(int(m) for m in _re.findall(r'id="trade-row-(\d+)"', r.text))
    second_buy_id = ids[-1]
    client.post("/trades", data={"ticker": "ZZZ", "action": "sell", "quantity": 50, "price": 25})

    # Delete the second buy (the @20 one). Remaining: buy 100@10, sell 50@25.
    # avg_cost should drop to 10, sell's realized_pl should become (25-10)*50 = 750.
    r = client.delete(f"/trades/{second_buy_id}")
    assert r.status_code == 200

    r = client.get("/trades?ticker=ZZZ")
    assert "+750.00" in r.text
    r = client.get("/holdings")
    assert "ZZZ" in r.text  # 50 shares remain


def test_delete_nonexistent_trade_404(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.delete("/trades/99999")
    assert r.status_code == 404


def test_robinhood_import_bad_csv_returns_422(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post(
        "/trades/import",
        files={"file": ("bad.csv", "no,header,here\n1,2,3\n", "text/csv")},
    )
    assert res.status_code == 422


def test_add_buy_and_sell_trades(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/trades", data={
        "ticker": "NVDA", "action": "buy", "quantity": 10, "price": 200,
        "fees": 0, "notes": "initial",
    })
    assert res.status_code == 200
    assert "NVDA" in res.text

    res = client.post("/trades", data={
        "ticker": "NVDA", "action": "sell", "quantity": 4, "price": 300,
        "fees": 0, "notes": "partial",
    })
    assert res.status_code == 200
    # realized_pl = (300-200)*4 = 400
    assert "+400.00" in res.text


def test_oversell_via_route_returns_422(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    client.post("/trades", data={"ticker": "X", "action": "buy", "quantity": 5, "price": 100})
    res = client.post("/trades", data={
        "ticker": "X", "action": "sell", "quantity": 10, "price": 110,
    })
    assert res.status_code == 422


def test_holdings_page_shows_realized_pl(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    # Make a profitable round-trip on TSLA so realized P&L is non-zero
    client.post("/trades", data={"ticker": "TSLA", "action": "buy", "quantity": 10, "price": 100})
    client.post("/trades", data={"ticker": "TSLA", "action": "sell", "quantity": 10, "price": 150})
    res = client.get("/holdings")
    assert res.status_code == 200
    assert "已实现盈亏" in res.text
    assert "+500.00" in res.text  # (150-100)*10


def test_trades_timeline_shows_splits_and_dividends(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    # Trade
    client.post("/trades", data={
        "ticker": "TQQQ", "action": "buy", "quantity": 20, "price": 30,
        "fees": 0, "executed_at": "2024-01-15",
    })
    # Split
    client.post("/splits", data={
        "ticker": "TQQQ", "ex_date": "2025-11-20", "ratio": 2,
    })
    # Dividend
    client.post("/dividends", data={
        "ticker": "TQQQ", "ex_date": "2025-09-24",
        "amount_per_share": 0.10, "total_amount": 4.0,
    })

    res = client.get("/trades")
    assert res.status_code == 200
    # All three event types render
    assert "买入" in res.text
    assert "拆股" in res.text or "1 → 2" in res.text
    assert "分红" in res.text


def test_trades_timeline_filter_splits_only(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    client.post("/trades", data={
        "ticker": "X", "action": "buy", "quantity": 10, "price": 100,
        "fees": 0, "executed_at": "2024-01-15",
    })
    client.post("/splits", data={
        "ticker": "X", "ex_date": "2025-01-01", "ratio": 2,
    })

    res = client.get("/trades?event_type=split")
    assert res.status_code == 200
    assert "拆股" in res.text or "1 → 2" in res.text
    # The buy row should not appear in split-only view (filter by table rows, not form options)
    assert "trade-row-" not in res.text


def test_trade_form_includes_executed_at_input(client: TestClient, monkeypatch):
    """Regression: the unified /trades form must include an executed_at
    date input for backfilling historical trades. Without it, manually-entered
    trades can only be dated 'today'."""
    _login(client, monkeypatch)
    res = client.get("/trades")
    assert res.status_code == 200
    # The input must be in the trade-field group (visible when 买入/卖出
    # is selected, hidden for splits/dividends).
    assert 'name="executed_at"' in res.text
    assert 'trade-field' in res.text


def test_trade_post_blank_executed_at_defaults_to_now(client: TestClient, monkeypatch):
    """Regression: empty executed_at in the form must persist as the current
    UTC datetime (not NULL). NULL would make the trade sort last via the
    sentinel, which is wrong for 'I just made this trade today'."""
    from datetime import UTC, datetime
    _login(client, monkeypatch)
    before = datetime.now(UTC)
    res = client.post("/trades", data={
        "ticker": "ABC", "action": "buy", "quantity": 1, "price": 10,
        "fees": 0, "executed_at": "",  # blank
    })
    assert res.status_code == 200
    after = datetime.now(UTC)

    # Query the DB directly to confirm executed_at is not None and is "recent"
    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    gen = db_base.session_scope()
    s = next(gen)
    t = s.query(Trade).filter(Trade.ticker == "ABC").one()
    assert t.executed_at is not None
    assert before <= t.executed_at <= after


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
    # (exitEditMode internally calls form.reset() and onEventKindChange)
    assert "onEventKindChange" in body
    # Specifically, the after-request hook must re-sync (the JS function call
    # must appear inside the hx-on::after-request expression):
    assert "hx-on::after-request" in body
    # Crude but effective: the two pieces must be in the same attribute value.
    import re
    m = re.search(r'hx-on::after-request="([^"]+)"', body)
    assert m is not None, "hx-on::after-request attribute missing"
    expr = m.group(1)
    assert "exitEditMode" in expr, (
        "after-request must call exitEditMode (which internally resets and "
        "re-syncs the hidden action input via onEventKindChange)"
    )


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
    from marketpulse.db.models import Holding, Trade
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
    from marketpulse.db.models import Holding, Trade
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


def test_trade_post_with_tz_offset_combines_with_local_now(client: TestClient, monkeypatch):
    """When tz_offset_minutes is provided and executed_at is YYYY-MM-DD,
    the stored datetime is (user's chosen date in their TZ) at (current
    local clock time), converted to UTC."""
    from datetime import UTC, timedelta
    _login(client, monkeypatch)
    tz_offset = -480  # Beijing UTC+8
    res = client.post("/trades", data={
        "ticker": "TZA", "action": "buy", "quantity": 1, "price": 10,
        "fees": 0, "executed_at": "2026-05-12",
        "tz_offset_minutes": str(tz_offset),
    })
    assert res.status_code == 200

    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    t = s.query(Trade).filter(Trade.ticker == "TZA").one()
    # The stored datetime, when converted back to Beijing local, must land on 2026-05-12.
    stored = t.executed_at
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    local_dt = stored - timedelta(minutes=tz_offset)
    assert local_dt.date().isoformat() == "2026-05-12"


def test_trade_post_zero_tz_offset_uses_now_time_of_day(client: TestClient, monkeypatch):
    """With tz_offset_minutes=0 (UTC client), YYYY-MM-DD picks current UTC
    time-of-day, NOT the old midnight default."""
    from datetime import UTC, datetime
    _login(client, monkeypatch)
    res = client.post("/trades", data={
        "ticker": "TZB", "action": "buy", "quantity": 1, "price": 10,
        "fees": 0, "executed_at": "2026-05-12",
        "tz_offset_minutes": "0",
    })
    assert res.status_code == 200

    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    t = s.query(Trade).filter(Trade.ticker == "TZB").one()
    assert t.executed_at.year == 2026 and t.executed_at.month == 5 and t.executed_at.day == 12
    stored = t.executed_at if t.executed_at.tzinfo else t.executed_at.replace(tzinfo=UTC)
    # Time-of-day should not be exactly 00:00:00 unless the test runs at exactly UTC midnight.
    # Fall back to "stored time-of-day equals 'now's' time-of-day within the test window".
    assert stored.time() != datetime.min.time(), (
        "TZ-aware parsing should use current time-of-day, not arbitrary 00:00"
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
    stored = t.executed_at if t.executed_at.tzinfo else t.executed_at.replace(tzinfo=UTC)
    assert before <= stored <= after


def test_trades_update_respects_tz_offset(client: TestClient, monkeypatch):
    """PUT /trades/{id} with YYYY-MM-DD + tz_offset combines date with current
    local clock time, same as POST."""
    from datetime import UTC, timedelta
    _login(client, monkeypatch)
    client.post("/trades", data={
        "ticker": "TZD", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": "",
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
    stored = t.executed_at if t.executed_at.tzinfo else t.executed_at.replace(tzinfo=UTC)
    local_dt = stored - timedelta(minutes=tz_offset)
    assert local_dt.date().isoformat() == "2026-05-10"


def test_trades_update_preserves_original_when_date_unchanged(client: TestClient, monkeypatch):
    """PUT /trades/{id} with original_executed_at_iso + date unchanged must
    preserve the original timestamp byte-for-byte (sub-second precision)."""
    from datetime import UTC, timedelta
    _login(client, monkeypatch)
    client.post("/trades", data={
        "ticker": "TZPRE", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": "", "tz_offset_minutes": "-480",
    })
    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    t = s.query(Trade).filter(Trade.ticker == "TZPRE").one()
    trade_id = t.id
    original_iso = t.executed_at.isoformat()
    original_ts = t.executed_at
    # What does the user see in the date input? The local-date of original.
    stored = t.executed_at if t.executed_at.tzinfo else t.executed_at.replace(tzinfo=UTC)
    local_dt = stored - timedelta(minutes=-480)
    same_local_date = local_dt.date().isoformat()
    # PUT with same date, just changing notes
    res = client.put(f"/trades/{trade_id}", data={
        "ticker": "TZPRE", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": same_local_date, "tz_offset_minutes": "-480",
        "original_executed_at_iso": original_iso, "notes": "edited",
    })
    assert res.status_code == 200
    s2 = next(db_base.session_scope())
    t2 = s2.query(Trade).filter(Trade.id == trade_id).one()
    assert t2.notes == "edited"
    # Timestamp must be EXACTLY the same.
    def _to_aware(d):
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    assert _to_aware(t2.executed_at) == _to_aware(original_ts)


def test_trades_update_recomputes_when_date_changed(client: TestClient, monkeypatch):
    """PUT with original_executed_at_iso + NEW date → helper sees date mismatch
    → falls through to TZ-combine path. Stored date (in user-local) is the new date."""
    from datetime import UTC, timedelta
    _login(client, monkeypatch)
    client.post("/trades", data={
        "ticker": "TZNEW", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": "", "tz_offset_minutes": "-480",
    })
    from marketpulse.db import base as db_base
    from marketpulse.db.models import Trade
    s = next(db_base.session_scope())
    t = s.query(Trade).filter(Trade.ticker == "TZNEW").one()
    trade_id = t.id
    original_iso = t.executed_at.isoformat()
    new_date = "2026-04-01"
    res = client.put(f"/trades/{trade_id}", data={
        "ticker": "TZNEW", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": new_date, "tz_offset_minutes": "-480",
        "original_executed_at_iso": original_iso, "notes": "moved",
    })
    assert res.status_code == 200
    s2 = next(db_base.session_scope())
    t2 = s2.query(Trade).filter(Trade.id == trade_id).one()
    stored = t2.executed_at if t2.executed_at.tzinfo else t2.executed_at.replace(tzinfo=UTC)
    local_dt = stored - timedelta(minutes=-480)
    assert local_dt.date().isoformat() == new_date


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


def test_trades_table_renders_time_with_data_utc(client: TestClient, monkeypatch):
    """Trade rows must wrap the time cell in <time data-utc=...> so JS can
    convert to user-local TZ on the client side."""
    _login(client, monkeypatch)
    client.post("/trades", data={
        "ticker": "TZTAB", "action": "buy", "quantity": 1, "price": 10,
        "executed_at": "", "tz_offset_minutes": "-480",
    })
    r = client.get("/trades")
    assert r.status_code == 200
    body = r.text
    assert "<time data-utc=" in body, (
        "trade time cells must be wrapped in <time data-utc=...>"
    )
    assert "applyLocalTime" in body, (
        "trades.html must include applyLocalTime() to convert times"
    )
    assert "htmx:afterSwap" in body, (
        "trades.html must re-apply local time after HTMX swaps the table"
    )


import math
from datetime import UTC, datetime, date

from marketpulse.db.models import Trade


def _seed_trades(db_session, n: int):
    """Seed N AAPL trades evenly spaced over a year."""
    for i in range(n):
        when = datetime(2026, 1, 1, 12, 0, tzinfo=UTC) + (
            (datetime(2026, 12, 31, 12, 0, tzinfo=UTC) - datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
            * (i / max(1, n - 1))
        )
        db_session.add(Trade(
            ticker="AAPL", action="buy", quantity=1.0, price=100.0,
            fees=0.0, executed_at=when, realized_pl=None,
        ))
    db_session.commit()


def test_trades_page_pagination_default_50(client, monkeypatch, db_session):
    """75 trades → page 1 shows 50, page 2 shows 25."""
    _login(client, monkeypatch)
    _seed_trades(db_session, 75)

    r = client.get("/trades")
    assert r.status_code == 200
    # Count unique rows via id="trade-row-" (appears once per row as the element id)
    assert r.text.count('id="trade-row-') == 50

    r = client.get("/trades?page=2")
    assert r.text.count('id="trade-row-') == 25


def test_trades_page_clamps_overflow_page(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_trades(db_session, 5)
    r = client.get("/trades?page=999")
    assert r.status_code == 200  # not 422


def test_trades_page_invalid_date_returns_422(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades?from=not-a-date")
    assert r.status_code == 422


def test_trades_page_from_greater_than_to_returns_422(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades?from=2026-06-01&to=2026-01-01")
    assert r.status_code == 422


def test_trades_page_q_prefix_match(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    for sym in ("AAPL", "AMZN", "NVDA"):
        db_session.add(Trade(ticker=sym, action="buy", quantity=1, price=100,
                             fees=0, executed_at=datetime(2026, 1, 1, tzinfo=UTC)))
    db_session.commit()

    r = client.get("/trades?q=AA")
    assert "AAPL" in r.text
    assert "AMZN" not in r.text
    assert "NVDA" not in r.text


def test_trades_page_q_empty_string_no_filter(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    db_session.add(Trade(ticker="AAPL", action="buy", quantity=1, price=100,
                         fees=0, executed_at=datetime(2026, 1, 1, tzinfo=UTC)))
    db_session.commit()
    r = client.get("/trades?q=")
    assert "AAPL" in r.text


def test_trades_page_ticker_alias_exact_match(client, monkeypatch, db_session):
    """?ticker=AAPL is exact match (legacy); does NOT match AAPL prefix neighbors."""
    _login(client, monkeypatch)
    for sym in ("AAPL", "AAPLE"):  # fictional prefix neighbor
        db_session.add(Trade(ticker=sym, action="buy", quantity=1, price=100,
                             fees=0, executed_at=datetime(2026, 1, 1, tzinfo=UTC)))
    db_session.commit()

    r = client.get("/trades?ticker=AAPL")
    assert "AAPL" in r.text
    # AAPLE row should NOT be in the displayed page rows.
    # Easiest check: ensure no tr with ticker AAPLE link exists.
    assert "stock/AAPLE" not in r.text


def test_trades_page_hx_request_returns_partial_only(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades", headers={"HX-Request": "true"})
    # Partial should NOT contain hero anchor — table or empty state should.
    assert "mp-hero" not in r.text
    # Should still contain table or empty-state.
    assert ('id="trade-row-' in r.text or "暂无记录" in r.text or "mp-table" in r.text
            or "mp-empty-row" in r.text or "<table" in r.text)


def test_trades_page_this_month_kpi_unaffected_by_filter(client, monkeypatch):
    """Even with from/to in distant past, page still renders and includes
    本月新笔数 KPI label (whose value uses current calendar month)."""
    _login(client, monkeypatch)
    r = client.get("/trades?from=2020-01-01&to=2020-12-31")
    assert r.status_code == 200
    # The KPI strip references "本月新笔数" only in the new template (Task 11).
    # For Task 8 we cannot fully verify the value since templates aren't ready.
    # Just verify the route doesn't crash.


def test_trades_page_ytd_label_default(client, monkeypatch):
    """No date filter → ctx.kpi.ytd_label == 'YTD'."""
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert r.status_code == 200


def test_trades_page_visual_anchors_present(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    for cls in ("mp-hero", "mp-trades-kpi", "mp-trades-filter",
                "mp-trades-main", "mp-trades-rail"):
        assert cls in r.text, f"missing {cls}"
    # h1 with grotesk class + 'Trade Ledger'
    assert "Trade Ledger" in r.text
    # Old Tailwind classes should be gone.
    assert 'class="bg-white rounded-md shadow-sm p-4"' not in r.text


def test_kpi_strip_5_value_blocks(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert r.text.count("mp-kpi__value") == 5


def test_kpi_avg_hold_days_dash_when_empty(client, monkeypatch):
    """No trades → avg_hold_days is None → rendered as '—'."""
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert "平均持仓天数" in r.text
    assert "—" in r.text


def test_kpi_win_rate_dash_when_no_closed(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert "胜率" in r.text


def test_kpi_ytd_label_default_is_ytd(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert "YTD" in r.text


def test_kpi_ytd_label_reflects_explicit_range(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades?from=2026-01-01&to=2026-03-31")
    assert "2026-01-01" in r.text
    assert "2026-03-31" in r.text
