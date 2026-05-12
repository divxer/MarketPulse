from datetime import date

import pytest

from marketpulse.holdings.splits import (
    SplitError,
    delete_split,
    get_splits_for_ticker,
    record_split,
)


def test_record_split_persists(db_session) -> None:
    s = record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=2.0)
    assert s.id is not None
    assert s.ticker == "TQQQ"
    assert s.ratio == 2.0
    assert s.source == "manual"


def test_record_split_normalizes_ticker(db_session) -> None:
    s = record_split(db_session, ticker="  tqqq ", ex_date=date(2025, 11, 20), ratio=2.0)
    assert s.ticker == "TQQQ"


def test_record_split_rejects_invalid_ratio(db_session) -> None:
    with pytest.raises(SplitError, match="ratio"):
        record_split(db_session, ticker="X", ex_date=date(2025, 1, 1), ratio=0)
    with pytest.raises(SplitError, match="ratio"):
        record_split(db_session, ticker="X", ex_date=date(2025, 1, 1), ratio=-1)
    with pytest.raises(SplitError, match="ratio"):
        record_split(db_session, ticker="X", ex_date=date(2025, 1, 1), ratio=1)


def test_record_split_rejects_empty_ticker(db_session) -> None:
    with pytest.raises(SplitError, match="ticker"):
        record_split(db_session, ticker="  ", ex_date=date(2025, 1, 1), ratio=2)


def test_record_split_duplicate_raises(db_session) -> None:
    record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=2.0)
    with pytest.raises(SplitError, match="already recorded"):
        record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=3.0)


def test_get_splits_for_ticker_returns_in_date_order(db_session) -> None:
    record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=2.0)
    record_split(db_session, ticker="TQQQ", ex_date=date(2022, 1, 13), ratio=2.0)
    splits = get_splits_for_ticker(db_session, "TQQQ")
    assert [s.ex_date for s in splits] == [date(2022, 1, 13), date(2025, 11, 20)]


def test_delete_split_returns_ticker(db_session) -> None:
    s = record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=2.0)
    t = delete_split(db_session, s.id)
    assert t == "TQQQ"
    assert get_splits_for_ticker(db_session, "TQQQ") == []


def test_delete_split_missing_raises(db_session) -> None:
    with pytest.raises(SplitError, match="not found"):
        delete_split(db_session, 9999)


def test_record_split_session_clean_after_duplicate(db_session) -> None:
    """After a duplicate raises and rolls back, the session must still be usable."""
    from datetime import date

    record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=2.0)
    with pytest.raises(SplitError, match="already recorded"):
        record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=3.0)
    # Different ex_date — must succeed, proving the session wasn't poisoned.
    s = record_split(db_session, ticker="TQQQ", ex_date=date(2024, 1, 13), ratio=2.0)
    assert s.id is not None
    assert len(get_splits_for_ticker(db_session, "TQQQ")) == 2


def test_record_split_stores_source(db_session) -> None:
    """Non-default source is persisted and returned."""
    from datetime import date

    s = record_split(db_session, ticker="X", ex_date=date(2025, 1, 1),
                     ratio=2.0, source="yfinance")
    assert s.source == "yfinance"
