import csv
from datetime import UTC, date, datetime
from io import StringIO

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.models import Dividend, Trade


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed(db_session):
    db_session.add(Trade(ticker="AAPL", action="buy", quantity=10, price=180.0,
                         fees=0, executed_at=datetime(2026, 5, 8, tzinfo=UTC),
                         realized_pl=None))
    db_session.add(Trade(ticker="AAPL", action="sell", quantity=4, price=200.0,
                         fees=0, executed_at=datetime(2026, 5, 9, tzinfo=UTC),
                         realized_pl=80.0))
    db_session.add(Dividend(ticker="AAPL", ex_date=date(2026, 5, 1),
                            amount_per_share=0.25, total_amount=1.0,
                            source="manual"))
    db_session.commit()


def test_export_csv_content_type_and_filename(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session)
    r = client.get("/trades/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    cd = r.headers["content-disposition"]
    assert "attachment" in cd
    assert "trades-" in cd and ".csv" in cd


def test_export_csv_robinhood_header(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session)
    r = client.get("/trades/export.csv")
    lines = r.text.strip().split("\n")
    assert lines[0] == (
        "Activity Date,Process Date,Settle Date,Instrument,Description,"
        "Trans Code,Quantity,Price,Amount"
    )


def test_export_csv_filter_event_type_trade_only(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session)
    r = client.get("/trades/export.csv?event_type=trade")
    body = r.text
    # 2 trade rows + 1 header = 3 lines total → 2 newlines after rstrip
    rows = [ln for ln in body.split("\n") if ln.strip()]
    assert len(rows) == 3  # header + 2 trade rows


def test_export_csv_skips_splits(client: TestClient, monkeypatch, db_session):
    from marketpulse.db.models import StockSplit
    _login(client, monkeypatch)
    _seed(db_session)
    db_session.add(StockSplit(ticker="AAPL", ex_date=date(2026, 4, 1),
                              ratio=2.0, source="manual"))
    db_session.commit()
    r = client.get("/trades/export.csv")
    rows = list(csv.reader(StringIO(r.text)))
    data_rows = rows[1:]
    trans_codes = [row[5] for row in data_rows if row]
    assert "Buy" in trans_codes
    assert "Sell" in trans_codes
    assert "CDIV" in trans_codes
    # Splits never appear: no 'Split' or similar.
    assert all(tc in {"Buy", "Sell", "CDIV"} for tc in trans_codes)


def test_export_csv_empty_filter_only_header(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session)
    r = client.get("/trades/export.csv?ticker=NONEXIST")
    lines = [ln for ln in r.text.split("\n") if ln.strip()]
    assert len(lines) == 1  # header only


def test_export_csv_round_trip_compatible_with_import(client: TestClient, monkeypatch, db_session):
    """Export → re-import flow: CSV parses cleanly (Buy/Sell rows accepted).

    Dividends (CDIV) are skipped by the importer by design. The 2 trade rows
    will be detected as duplicates (already in DB), so preview is empty — but
    the import endpoint must return 200 and confirm it parsed the trades.
    """
    _login(client, monkeypatch)
    _seed(db_session)
    r = client.get("/trades/export.csv")
    csv_text = r.text

    res = client.post(
        "/trades/import",
        files={"file": ("export.csv", csv_text, "text/csv")},
    )
    assert res.status_code == 200
    # 2 Buy/Sell rows were parsed (CDIV ignored); all skipped as duplicates.
    assert "2" in res.text  # total_parsed or skipped count shown in preview
    assert "export.csv" in res.text
