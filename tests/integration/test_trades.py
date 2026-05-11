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
        record_trade(db_session, ticker="X", action="buy", quantity=1, price=0)
    with pytest.raises(TradeError, match="fees"):
        record_trade(db_session, ticker="X", action="buy", quantity=1, price=10, fees=-1)


def test_total_realized_pl_sums_across_trades(db_session: Session) -> None:
    record_trade(db_session, ticker="A", action="buy", quantity=10, price=100)
    record_trade(db_session, ticker="A", action="sell", quantity=5, price=120)  # +100
    record_trade(db_session, ticker="B", action="buy", quantity=10, price=50)
    record_trade(db_session, ticker="B", action="sell", quantity=5, price=40)   # -50
    assert total_realized_pl(db_session) == 50
    assert total_realized_pl(db_session, ticker="A") == 100
    assert total_realized_pl(db_session, ticker="B") == -50
