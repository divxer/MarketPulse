import pytest

from marketpulse.holdings.robinhood_import import (
    RobinhoodParseError,
    parse_robinhood_csv,
)

_HEADER = (
    "Activity Date,Process Date,Settle Date,Instrument,Description,"
    "Trans Code,Quantity,Price,Amount\n"
)


def test_parses_buy_and_sell_rows() -> None:
    csv = _HEADER + (
        "5/8/2026,5/9/2026,5/12/2026,AAPL,Apple Inc.,Buy,10,$180.50,($1805.00)\n"
        "5/9/2026,5/10/2026,5/13/2026,AAPL,Apple Inc.,Sell,4,$185.00,$740.00\n"
    )
    trades = parse_robinhood_csv(csv)
    assert len(trades) == 2
    assert trades[0].ticker == "AAPL"
    assert trades[0].action == "buy"
    assert trades[0].quantity == 10
    assert trades[0].price == 180.50
    assert trades[0].executed_at.year == 2026
    assert trades[1].action == "sell"


def test_filters_out_non_trade_rows() -> None:
    csv = _HEADER + (
        "5/1/2026,5/2/2026,5/3/2026,,ACH Deposit,ACH,,,$1000.00\n"
        "5/2/2026,5/3/2026,5/4/2026,AAPL,Dividend,CDIV,,,$2.34\n"
        "5/3/2026,5/4/2026,5/5/2026,AAPL,Stock Split,SPL,40,,\n"
        "5/8/2026,5/9/2026,5/12/2026,TQQQ,ProShares,Buy,5,$76.28,($381.40)\n"
    )
    trades = parse_robinhood_csv(csv)
    assert len(trades) == 1
    assert trades[0].ticker == "TQQQ"


def test_handles_money_with_commas_and_parens() -> None:
    csv = _HEADER + (
        "5/8/2026,5/9/2026,5/12/2026,NVDA,Nvidia,Buy,100,\"$1,234.56\",\"($123,456.00)\"\n"
    )
    trades = parse_robinhood_csv(csv)
    assert trades[0].price == 1234.56


def test_missing_required_column_raises() -> None:
    csv = "Activity Date,Instrument,Trans Code\n"
    with pytest.raises(RobinhoodParseError, match="missing required columns"):
        parse_robinhood_csv(csv)


def test_empty_csv_raises() -> None:
    with pytest.raises(RobinhoodParseError, match="empty"):
        parse_robinhood_csv("")


def test_malformed_buy_row_raises_with_row_number() -> None:
    csv = _HEADER + (
        "5/8/2026,5/9/2026,5/12/2026,AAPL,Apple,Buy,abc,$180,($180)\n"
    )
    with pytest.raises(RobinhoodParseError, match="row 2"):
        parse_robinhood_csv(csv)


def test_iso_date_format_also_accepted() -> None:
    csv = _HEADER + "2026-05-08,2026-05-09,2026-05-12,SPY,SPDR,Buy,1,$500,($500)\n"
    trades = parse_robinhood_csv(csv)
    assert trades[0].executed_at.year == 2026
    assert trades[0].executed_at.month == 5
    assert trades[0].executed_at.day == 8


def test_bom_prefixed_csv() -> None:
    csv = ("﻿" + _HEADER).encode("utf-8") + (
        b"5/8/2026,5/9/2026,5/12/2026,AAPL,Apple,Buy,10,$180,($1800)\n"
    )
    trades = parse_robinhood_csv(csv)
    assert len(trades) == 1
