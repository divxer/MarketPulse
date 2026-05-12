import pytest
from sqlalchemy.orm import Session

from marketpulse.db.models import Holding, Trade
from marketpulse.holdings.trades import TradeError, record_trade, total_realized_pl


def test_buy_creates_holding(db_session: Session) -> None:
    t = record_trade(db_session, ticker="NVDA", action="buy", quantity=10, price=200)
    assert t.id is not None
    assert t.realized_pl is None
    h = db_session.query(Holding).filter_by(ticker="NVDA").one()
    assert h.quantity == 10
    assert h.avg_cost == 200


def test_second_buy_averages_cost(db_session: Session) -> None:
    record_trade(db_session, ticker="NVDA", action="buy", quantity=10, price=200)
    record_trade(db_session, ticker="NVDA", action="buy", quantity=10, price=300)
    h = db_session.query(Holding).filter_by(ticker="NVDA").one()
    assert h.quantity == 20
    assert h.avg_cost == 250  # (10*200 + 10*300) / 20


def test_buy_includes_fees_in_cost_basis(db_session: Session) -> None:
    record_trade(db_session, ticker="AAPL", action="buy", quantity=10, price=180, fees=5)
    h = db_session.query(Holding).filter_by(ticker="AAPL").one()
    assert h.avg_cost == 180.5  # 180 + 5/10


def test_sell_records_realized_pl(db_session: Session) -> None:
    record_trade(db_session, ticker="NVDA", action="buy", quantity=10, price=200)
    t = record_trade(db_session, ticker="NVDA", action="sell", quantity=5, price=300)
    assert t.realized_pl == 500  # (300-200) * 5
    h = db_session.query(Holding).filter_by(ticker="NVDA").one()
    assert h.quantity == 5
    assert h.avg_cost == 200  # avg_cost preserved on partial sell


def test_sell_with_fees(db_session: Session) -> None:
    record_trade(db_session, ticker="META", action="buy", quantity=10, price=300)
    t = record_trade(db_session, ticker="META", action="sell", quantity=3, price=400, fees=10)
    assert t.realized_pl == pytest.approx(290)  # (400-300)*3 - 10


def test_sell_entire_position_deletes_holding(db_session: Session) -> None:
    record_trade(db_session, ticker="TSLA", action="buy", quantity=5, price=250)
    record_trade(db_session, ticker="TSLA", action="sell", quantity=5, price=300)
    assert db_session.query(Holding).filter_by(ticker="TSLA").one_or_none() is None
    trades = db_session.query(Trade).filter_by(ticker="TSLA").all()
    assert len(trades) == 2


def test_oversell_rejected(db_session: Session) -> None:
    record_trade(db_session, ticker="AMD", action="buy", quantity=10, price=150)
    with pytest.raises(TradeError, match="cannot sell"):
        record_trade(db_session, ticker="AMD", action="sell", quantity=15, price=200)


def test_sell_without_holding_rejected(db_session: Session) -> None:
    with pytest.raises(TradeError, match="cannot sell"):
        record_trade(db_session, ticker="GME", action="sell", quantity=1, price=100)


def test_invalid_action_rejected(db_session: Session) -> None:
    with pytest.raises(TradeError, match="invalid action"):
        record_trade(db_session, ticker="X", action="hold", quantity=1, price=10)


def test_non_positive_values_rejected(db_session: Session) -> None:
    with pytest.raises(TradeError, match="quantity"):
        record_trade(db_session, ticker="X", action="buy", quantity=0, price=10)
    with pytest.raises(TradeError, match="price"):
        record_trade(db_session, ticker="X", action="buy", quantity=1, price=-5)
    with pytest.raises(TradeError, match="fees"):
        record_trade(db_session, ticker="X", action="buy", quantity=1, price=10, fees=-1)


def test_zero_price_buy_allowed_for_splits(db_session: Session) -> None:
    """price=0 is valid: represents stock splits, gifts, or share dividends."""
    record_trade(db_session, ticker="X", action="buy", quantity=10, price=10)
    record_trade(db_session, ticker="X", action="buy", quantity=10, price=0,
                 notes="1:2 split adjustment")
    from marketpulse.db.models import Holding
    h = db_session.query(Holding).filter(Holding.ticker == "X").one()
    assert h.quantity == 20
    assert h.avg_cost == 5.0  # $100 total cost / 20 shares


def test_total_realized_pl_sums_across_trades(db_session: Session) -> None:
    record_trade(db_session, ticker="A", action="buy", quantity=10, price=100)
    record_trade(db_session, ticker="A", action="sell", quantity=5, price=120)  # +100
    record_trade(db_session, ticker="B", action="buy", quantity=10, price=50)
    record_trade(db_session, ticker="B", action="sell", quantity=5, price=40)   # -50
    assert total_realized_pl(db_session) == 50
    assert total_realized_pl(db_session, ticker="A") == 100
    assert total_realized_pl(db_session, ticker="B") == -50


def test_recompute_applies_forward_split(db_session) -> None:
    """1:2 forward split doubles share count and halves avg cost."""
    from datetime import UTC, date, datetime

    from marketpulse.db.models import Holding
    from marketpulse.holdings.splits import record_split
    from marketpulse.holdings.trades import recompute_ticker

    record_trade(db_session, ticker="TQQQ", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=2.0)
    recompute_ticker(db_session, "TQQQ")

    h = db_session.query(Holding).filter_by(ticker="TQQQ").one()
    assert h.quantity == 40
    assert h.avg_cost == 15.0


def test_recompute_applies_reverse_split(db_session) -> None:
    """5:1 reverse split (ratio 0.2) cuts shares to 20%, raises avg cost 5x."""
    from datetime import UTC, date, datetime

    from marketpulse.db.models import Holding
    from marketpulse.holdings.splits import record_split
    from marketpulse.holdings.trades import recompute_ticker

    record_trade(db_session, ticker="X", action="buy", quantity=100, price=10,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_split(db_session, ticker="X", ex_date=date(2025, 1, 1), ratio=0.2)
    recompute_ticker(db_session, "X")

    h = db_session.query(Holding).filter_by(ticker="X").one()
    assert h.quantity == pytest.approx(20)
    assert h.avg_cost == pytest.approx(50)


def test_recompute_applies_consecutive_splits(db_session) -> None:
    """Two splits compound: 1:2 then 1:3 on 10 shares = 60 shares."""
    from datetime import UTC, date, datetime

    from marketpulse.db.models import Holding
    from marketpulse.holdings.splits import record_split
    from marketpulse.holdings.trades import recompute_ticker

    record_trade(db_session, ticker="X", action="buy", quantity=10, price=60,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_split(db_session, ticker="X", ex_date=date(2024, 6, 1), ratio=2.0)
    record_split(db_session, ticker="X", ex_date=date(2025, 1, 1), ratio=3.0)
    recompute_ticker(db_session, "X")

    h = db_session.query(Holding).filter_by(ticker="X").one()
    assert h.quantity == 60
    assert h.avg_cost == pytest.approx(10.0)


def test_same_day_trade_executes_before_split(db_session) -> None:
    """A trade on the same date as the split sorts BEFORE the split."""
    from datetime import UTC, date, datetime

    from marketpulse.db.models import Holding
    from marketpulse.holdings.splits import record_split
    from marketpulse.holdings.trades import recompute_ticker

    record_trade(db_session, ticker="X", action="buy", quantity=10, price=60,
                 executed_at=datetime(2025, 11, 20, 9, 30, tzinfo=UTC))
    record_split(db_session, ticker="X", ex_date=date(2025, 11, 20), ratio=2.0)
    recompute_ticker(db_session, "X")

    h = db_session.query(Holding).filter_by(ticker="X").one()
    assert h.quantity == 20
    assert h.avg_cost == 30.0


def test_recompute_handles_sell_after_split(db_session) -> None:
    """Sells use POST-split avg_cost when computing realized P&L."""
    from datetime import UTC, date, datetime

    from marketpulse.db.models import Trade
    from marketpulse.holdings.splits import record_split
    from marketpulse.holdings.trades import recompute_ticker

    record_trade(db_session, ticker="X", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_split(db_session, ticker="X", ex_date=date(2025, 6, 1), ratio=2.0)
    record_trade(db_session, ticker="X", action="sell", quantity=10, price=20,
                 executed_at=datetime(2025, 7, 1, tzinfo=UTC))
    recompute_ticker(db_session, "X")

    sell = (
        db_session.query(Trade)
        .filter(Trade.ticker == "X", Trade.action == "sell")
        .one()
    )
    assert sell.realized_pl == pytest.approx(50.0)


def test_recompute_after_split_delete_restores(db_session) -> None:
    """Delete a split → recompute → state matches as if split never existed."""
    from datetime import UTC, date, datetime

    from marketpulse.db.models import Holding
    from marketpulse.holdings.splits import delete_split, record_split
    from marketpulse.holdings.trades import recompute_ticker

    record_trade(db_session, ticker="X", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    s = record_split(db_session, ticker="X", ex_date=date(2025, 6, 1), ratio=2.0)
    recompute_ticker(db_session, "X")
    assert db_session.query(Holding).filter_by(ticker="X").one().quantity == 40

    delete_split(db_session, s.id)
    recompute_ticker(db_session, "X")
    h = db_session.query(Holding).filter_by(ticker="X").one()
    assert h.quantity == 20
    assert h.avg_cost == 30.0


def test_recompute_orders_null_executed_at_last(db_session) -> None:
    """Trade with executed_at=None must sort AFTER trades that have one,
    even if its created_at is earlier. Preserves the old SQL NULLS LAST contract."""
    from datetime import UTC, datetime

    from marketpulse.db.models import Holding
    from marketpulse.holdings.trades import recompute_ticker

    # First, insert a trade with no executed_at — DB created_at = now.
    record_trade(db_session, ticker="X", action="buy", quantity=10, price=100)
    # Then a trade with explicit older executed_at.
    record_trade(db_session, ticker="X", action="buy", quantity=10, price=50,
                 executed_at=datetime(2020, 1, 1, tzinfo=UTC))
    recompute_ticker(db_session, "X")

    h = db_session.query(Holding).filter_by(ticker="X").one()
    # Expected walk: first the 2020 buy @ $50 (qty=10, avg=50),
    # then the no-executed_at buy @ $100 (qty=20, avg=75).
    assert h.quantity == 20
    assert h.avg_cost == 75.0


def test_same_day_sell_executes_before_split(db_session) -> None:
    """A sell on the same date as a split uses PRE-split avg_cost.
    Sells at 09:30 UTC sort before the EOD split anchor.
    """
    from datetime import UTC, date, datetime

    from marketpulse.db.models import Trade
    from marketpulse.holdings.splits import record_split
    from marketpulse.holdings.trades import recompute_ticker

    # Buy 100 @ $10 on a prior date.
    record_trade(db_session, ticker="X", action="buy", quantity=100, price=10,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    # Sell 20 @ $6 at 09:30 UTC on split day.
    record_trade(db_session, ticker="X", action="sell", quantity=20, price=6,
                 executed_at=datetime(2025, 11, 20, 9, 30, tzinfo=UTC))
    # 1:2 split same date.
    record_split(db_session, ticker="X", ex_date=date(2025, 11, 20), ratio=2.0)
    recompute_ticker(db_session, "X")

    sell = (
        db_session.query(Trade)
        .filter(Trade.ticker == "X", Trade.action == "sell")
        .one()
    )
    # Pre-split avg_cost = $10. Realized = (6 - 10) * 20 = -80.
    assert sell.realized_pl == pytest.approx(-80.0)


def test_fractional_shares_after_reverse_split_precise(db_session) -> None:
    """7 shares × 0.2 (5:1 reverse) = 1.4 shares; float64 should preserve this."""
    from datetime import UTC, date, datetime

    from marketpulse.db.models import Holding
    from marketpulse.holdings.splits import record_split
    from marketpulse.holdings.trades import recompute_ticker

    record_trade(db_session, ticker="X", action="buy", quantity=7, price=10,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_split(db_session, ticker="X", ex_date=date(2025, 1, 1), ratio=0.2)
    recompute_ticker(db_session, "X")

    h = db_session.query(Holding).filter_by(ticker="X").one()
    assert h.quantity == pytest.approx(1.4)
    assert h.avg_cost == pytest.approx(50.0)
