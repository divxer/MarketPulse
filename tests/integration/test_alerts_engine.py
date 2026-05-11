from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from marketpulse.alerts.engine import evaluate_rules
from marketpulse.data.types import Quote
from marketpulse.db.models import AlertRule


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, body: str, url: str | None = None) -> bool:
        self.sent.append((title, body))
        return True


class FakeData:
    def __init__(self, quotes: dict[str, Quote] | None = None) -> None:
        self.quotes = quotes or {}
        self.failing: set[str] = set()

    def get_quote(self, ticker: str) -> Quote:
        if ticker in self.failing:
            raise RuntimeError("yfinance down")
        if ticker not in self.quotes:
            raise KeyError(ticker)
        return self.quotes[ticker]


def _quote(ticker: str, price: float, change_pct: float = 0.0,
           volume: int = 1_000_000, avg_volume: int = 1_000_000) -> Quote:
    return Quote(
        ticker=ticker, price=price, change_pct=change_pct,
        volume=volume, avg_volume_20d=avg_volume,
        fetched_at=datetime.now(UTC),
    )


def _add_rule(db: Session, **kwargs: Any) -> AlertRule:
    rule = AlertRule(**kwargs)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def test_price_threshold_fires(db_session: Session) -> None:
    _add_rule(db_session, ticker="NVDA", metric="price", op=">=", threshold=200)
    data = FakeData({"NVDA": _quote("NVDA", price=215)})
    notifier = FakeNotifier()
    results = evaluate_rules(db_session, data=data, notifier=notifier)
    assert len(results) == 1
    assert results[0]["fired"] is True
    assert len(notifier.sent) == 1
    assert "NVDA" in notifier.sent[0][0]


def test_price_threshold_not_matched(db_session: Session) -> None:
    _add_rule(db_session, ticker="NVDA", metric="price", op=">=", threshold=300)
    data = FakeData({"NVDA": _quote("NVDA", price=215)})
    notifier = FakeNotifier()
    results = evaluate_rules(db_session, data=data, notifier=notifier)
    assert results[0]["fired"] is False
    assert results[0]["reason"] == "not-matched"
    assert notifier.sent == []


def test_change_pct_below_threshold(db_session: Session) -> None:
    _add_rule(db_session, ticker="TSLA", metric="change_pct", op="<=", threshold=-3.0)
    data = FakeData({"TSLA": _quote("TSLA", price=200, change_pct=-5.5)})
    notifier = FakeNotifier()
    results = evaluate_rules(db_session, data=data, notifier=notifier)
    assert results[0]["fired"] is True


def test_volume_ratio_threshold(db_session: Session) -> None:
    _add_rule(db_session, ticker="TQQQ", metric="volume_ratio", op=">=", threshold=2.0)
    data = FakeData({"TQQQ": _quote("TQQQ", price=80, volume=3_000_000, avg_volume=1_000_000)})
    notifier = FakeNotifier()
    results = evaluate_rules(db_session, data=data, notifier=notifier)
    assert results[0]["fired"] is True
    assert "量比" in notifier.sent[0][1]


def test_debounce_blocks_recent_fire(db_session: Session) -> None:
    now = datetime.now(UTC)
    rule = _add_rule(
        db_session, ticker="X", metric="price", op=">=", threshold=100,
        last_triggered_at=now - timedelta(minutes=30),
    )
    data = FakeData({"X": _quote("X", price=150)})
    notifier = FakeNotifier()
    results = evaluate_rules(
        db_session, data=data, notifier=notifier, debounce_minutes=60,
    )
    assert results[0]["fired"] is False
    assert results[0]["reason"] == "debounced"
    assert notifier.sent == []
    # last_triggered_at unchanged
    db_session.refresh(rule)
    assert (now - rule.last_triggered_at).total_seconds() > 60


def test_debounce_allows_fire_after_window(db_session: Session) -> None:
    _add_rule(
        db_session, ticker="X", metric="price", op=">=", threshold=100,
        last_triggered_at=datetime.now(UTC) - timedelta(hours=2),
    )
    data = FakeData({"X": _quote("X", price=150)})
    notifier = FakeNotifier()
    results = evaluate_rules(
        db_session, data=data, notifier=notifier, debounce_minutes=60,
    )
    assert results[0]["fired"] is True


def test_disabled_rule_not_evaluated(db_session: Session) -> None:
    _add_rule(
        db_session, ticker="X", metric="price", op=">=", threshold=100, enabled=False,
    )
    data = FakeData({"X": _quote("X", price=150)})
    notifier = FakeNotifier()
    results = evaluate_rules(db_session, data=data, notifier=notifier)
    assert results == []
    assert notifier.sent == []


def test_quote_failure_skips_rule(db_session: Session) -> None:
    _add_rule(db_session, ticker="BROKEN", metric="price", op=">=", threshold=100)
    data = FakeData()
    data.failing.add("BROKEN")
    notifier = FakeNotifier()
    results = evaluate_rules(db_session, data=data, notifier=notifier)
    assert results[0]["fired"] is False
    assert "quote-failed" in results[0]["reason"]
    assert notifier.sent == []


def test_persists_last_value_and_timestamp_after_fire(db_session: Session) -> None:
    rule = _add_rule(db_session, ticker="NVDA", metric="price", op=">=", threshold=200)
    data = FakeData({"NVDA": _quote("NVDA", price=215.5)})
    evaluate_rules(db_session, data=data, notifier=FakeNotifier())
    db_session.refresh(rule)
    assert rule.last_value == 215.5
    assert rule.last_triggered_at is not None
