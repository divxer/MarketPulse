from datetime import date

import pytest

from marketpulse.holdings.dividends import (
    DividendError,
    monthly_dividends,
    per_ticker_dividends,
    record_dividend,
    total_dividends,
)


def test_record_dividend_persists(db_session) -> None:
    d = record_dividend(
        db_session, ticker="TQQQ", ex_date=date(2024, 3, 20),
        amount_per_share=0.22, total_amount=6.02,
    )
    assert d.id is not None
    assert d.ticker == "TQQQ"
    assert d.amount_per_share == 0.22
    assert d.total_amount == 6.02


def test_record_dividend_rejects_negative(db_session) -> None:
    with pytest.raises(DividendError):
        record_dividend(db_session, ticker="X", ex_date=date(2024, 1, 1),
                        amount_per_share=-0.1, total_amount=10)
    with pytest.raises(DividendError):
        record_dividend(db_session, ticker="X", ex_date=date(2024, 1, 1),
                        amount_per_share=0.1, total_amount=-1)


def test_record_dividend_requires_ticker(db_session) -> None:
    with pytest.raises(DividendError, match="ticker"):
        record_dividend(db_session, ticker="  ", ex_date=date(2024, 1, 1),
                        amount_per_share=0.1, total_amount=1)


def test_total_dividends_all_and_per_ticker(db_session) -> None:
    record_dividend(db_session, ticker="TQQQ", ex_date=date(2024, 3, 20),
                    amount_per_share=0.22, total_amount=6.02)
    record_dividend(db_session, ticker="TQQQ", ex_date=date(2024, 6, 26),
                    amount_per_share=0.28, total_amount=24.88)
    record_dividend(db_session, ticker="QBTS", ex_date=date(2024, 5, 1),
                    amount_per_share=0.05, total_amount=7.20)

    assert abs(total_dividends(db_session) - (6.02 + 24.88 + 7.20)) < 1e-9
    assert abs(total_dividends(db_session, ticker="TQQQ") - (6.02 + 24.88)) < 1e-9
    assert abs(total_dividends(db_session, ticker="QBTS") - 7.20) < 1e-9


def test_per_ticker_dividends(db_session) -> None:
    record_dividend(db_session, ticker="TQQQ", ex_date=date(2024, 3, 20),
                    amount_per_share=0.22, total_amount=6.02)
    record_dividend(db_session, ticker="QBTS", ex_date=date(2024, 5, 1),
                    amount_per_share=0.05, total_amount=7.20)
    m = per_ticker_dividends(db_session)
    assert m == {"TQQQ": 6.02, "QBTS": 7.20}


def test_monthly_dividends_groups_and_sorts(db_session) -> None:
    record_dividend(db_session, ticker="TQQQ", ex_date=date(2024, 3, 20),
                    amount_per_share=0.22, total_amount=6.02)
    record_dividend(db_session, ticker="QBTS", ex_date=date(2024, 3, 25),
                    amount_per_share=0.05, total_amount=3.10)
    record_dividend(db_session, ticker="TQQQ", ex_date=date(2024, 6, 26),
                    amount_per_share=0.28, total_amount=24.88)
    rows = monthly_dividends(db_session)
    assert len(rows) == 2
    assert rows[0]["month"] == "2024-03"
    assert abs(rows[0]["amount"] - 9.12) < 1e-9
    assert rows[1]["month"] == "2024-06"
    assert abs(rows[1]["amount"] - 24.88) < 1e-9


def test_record_dividend_persists_source(db_session) -> None:
    """Non-default source is persisted and round-trips."""
    d = record_dividend(
        db_session, ticker="TQQQ", ex_date=date(2025, 9, 24),
        amount_per_share=0.10, total_amount=2.00, source="tencent",
    )
    assert d.source == "tencent"


def test_record_dividend_duplicate_raises(db_session) -> None:
    """(ticker, ex_date) duplicate → DividendError 'already recorded'."""
    record_dividend(
        db_session, ticker="TQQQ", ex_date=date(2025, 9, 24),
        amount_per_share=0.10, total_amount=2.00,
    )
    with pytest.raises(DividendError, match="already recorded"):
        record_dividend(
            db_session, ticker="TQQQ", ex_date=date(2025, 9, 24),
            amount_per_share=0.12, total_amount=2.40,
        )


def test_record_dividend_session_clean_after_duplicate(db_session) -> None:
    """After a duplicate raises, the session must still be usable."""
    record_dividend(
        db_session, ticker="TQQQ", ex_date=date(2025, 9, 24),
        amount_per_share=0.10, total_amount=2.00,
    )
    with pytest.raises(DividendError, match="already recorded"):
        record_dividend(
            db_session, ticker="TQQQ", ex_date=date(2025, 9, 24),
            amount_per_share=0.12, total_amount=2.40,
        )
    # Different ex_date — must succeed.
    d = record_dividend(
        db_session, ticker="TQQQ", ex_date=date(2025, 12, 24),
        amount_per_share=0.09, total_amount=3.42,
    )
    assert d.id is not None


def test_delete_dividend_returns_ticker(db_session) -> None:
    from marketpulse.holdings.dividends import delete_dividend

    d = record_dividend(
        db_session, ticker="TQQQ", ex_date=date(2025, 9, 24),
        amount_per_share=0.10, total_amount=2.00,
    )
    t = delete_dividend(db_session, d.id)
    assert t == "TQQQ"
    assert total_dividends(db_session, ticker="TQQQ") == 0


def test_delete_dividend_missing_raises(db_session) -> None:
    from marketpulse.holdings.dividends import delete_dividend

    with pytest.raises(DividendError, match="not found"):
        delete_dividend(db_session, 9999)
