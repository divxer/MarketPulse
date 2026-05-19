"""Hit-rate scoring functions over EvaluationEvent + EvaluationOutcome."""
from datetime import UTC, date, datetime, timedelta

import pytest

from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


def _ev(db, *, ticker="AAPL", subtype="bullish", source="stock_analysis",
        days_ago=10, price=100.0):
    e = EvaluationEvent(
        event_type="ai_analysis",
        subtype=subtype,
        ticker=ticker,
        event_time=datetime.now(UTC) - timedelta(days=days_ago),
        event_price=price,
        payload={"source": source, "prompt_version": "v3"},
    )
    db.add(e)
    db.flush()
    return e


def _out(db, event, *, horizon=5, excess=0.02):
    """Helper to attach an outcome with a given excess_return."""
    o = EvaluationOutcome(
        event_id=event.id,
        horizon_trading_days=horizon,
        event_price=event.event_price,
        horizon_price=event.event_price * (1 + excess + 0.001),
        horizon_date=date.today(),
        forward_return=excess + 0.001,
        benchmark_ticker="SPY",
        benchmark_forward_return=0.001,
        excess_return=excess,
    )
    db.add(o)
    db.flush()
    return o


def test_compute_hit_rate_bullish_excess_positive_is_hit(db_session):
    from marketpulse.evaluation.scoring import compute_hit_rate

    e = _ev(db_session, subtype="bullish")
    _out(db_session, e, horizon=5, excess=0.03)   # excess > +1% threshold → hit
    db_session.commit()

    stats = compute_hit_rate(db_session, horizon=5)
    assert stats.n_total == 1
    assert stats.n_hits == 1
    assert stats.hit_rate == pytest.approx(1.0)


def test_compute_hit_rate_bearish_excess_negative_is_hit(db_session):
    from marketpulse.evaluation.scoring import compute_hit_rate

    e = _ev(db_session, subtype="bearish")
    _out(db_session, e, horizon=5, excess=-0.05)
    db_session.commit()

    stats = compute_hit_rate(db_session, horizon=5)
    assert stats.n_hits == 1


def test_compute_hit_rate_neutral_within_threshold_is_hit(db_session):
    from marketpulse.evaluation.scoring import compute_hit_rate

    e = _ev(db_session, subtype="neutral")
    _out(db_session, e, horizon=5, excess=0.005)   # |0.5%| <= 1% → hit
    db_session.commit()

    stats = compute_hit_rate(db_session, horizon=5)
    assert stats.n_hits == 1


def test_compute_hit_rate_boundary_at_threshold(db_session):
    """Excess exactly +1% with bullish verdict → miss (strict >).
    Excess exactly +1% with neutral verdict → hit (inclusive <=)."""
    from marketpulse.evaluation.scoring import compute_hit_rate

    e1 = _ev(db_session, ticker="AAA", subtype="bullish")
    _out(db_session, e1, excess=0.01)
    e2 = _ev(db_session, ticker="BBB", subtype="neutral")
    _out(db_session, e2, excess=0.01)
    db_session.commit()

    stats = compute_hit_rate(db_session, horizon=5)
    # bullish missed (0.01 > 0.01 = False), neutral hit (|0.01| <= 0.01 = True)
    assert stats.n_hits == 1


def test_compute_hit_rate_excludes_events_without_outcome(db_session):
    from marketpulse.evaluation.scoring import compute_hit_rate

    _ev(db_session, subtype="bullish")  # no outcome attached
    db_session.commit()

    stats = compute_hit_rate(db_session, horizon=5)
    assert stats.n_total == 0
    assert stats.hit_rate is None


def test_compute_hit_rate_filters_by_ticker(db_session):
    from marketpulse.evaluation.scoring import compute_hit_rate

    e1 = _ev(db_session, ticker="AAPL", subtype="bullish")
    _out(db_session, e1, excess=0.03)
    e2 = _ev(db_session, ticker="NVDA", subtype="bullish")
    _out(db_session, e2, excess=0.03)
    db_session.commit()

    stats = compute_hit_rate(db_session, horizon=5, ticker="AAPL")
    assert stats.n_total == 1


def test_compute_hit_rate_filters_by_horizon(db_session):
    from marketpulse.evaluation.scoring import compute_hit_rate

    e = _ev(db_session)
    _out(db_session, e, horizon=5, excess=0.03)
    _out(db_session, e, horizon=20, excess=-0.01)
    db_session.commit()

    stats_5 = compute_hit_rate(db_session, horizon=5)
    assert stats_5.n_total == 1
    stats_20 = compute_hit_rate(db_session, horizon=20)
    assert stats_20.n_total == 1


def test_compute_hit_rate_filters_by_source_in_payload(db_session):
    from marketpulse.evaluation.scoring import compute_hit_rate

    e1 = _ev(db_session, source="stock_analysis")
    _out(db_session, e1, excess=0.03)
    e2 = _ev(db_session, ticker="NVDA", source="recap")
    _out(db_session, e2, excess=0.03)
    db_session.commit()

    stats = compute_hit_rate(db_session, horizon=5, source="recap")
    assert stats.n_total == 1
    assert stats.n_hits == 1


def test_compute_hit_rate_filters_by_since_date(db_session):
    from marketpulse.evaluation.scoring import compute_hit_rate

    e_old = _ev(db_session, days_ago=120)
    _out(db_session, e_old, excess=0.03)
    e_new = _ev(db_session, days_ago=10, ticker="NVDA")
    _out(db_session, e_new, excess=0.03)
    db_session.commit()

    cutoff = date.today() - timedelta(days=90)
    stats = compute_hit_rate(db_session, horizon=5, since=cutoff)
    assert stats.n_total == 1


def test_compute_hit_rate_returns_none_hit_rate_when_n_zero(db_session):
    from marketpulse.evaluation.scoring import compute_hit_rate
    stats = compute_hit_rate(db_session, horizon=5)
    assert stats.n_total == 0
    assert stats.hit_rate is None
    assert stats.avg_excess_return == 0.0


def test_compute_hit_rate_avg_excess_is_simple_mean(db_session):
    """avg_excess_return is simple mean (no sign flip for bearish)."""
    from marketpulse.evaluation.scoring import compute_hit_rate

    e1 = _ev(db_session, subtype="bullish")
    _out(db_session, e1, excess=0.03)
    e2 = _ev(db_session, ticker="NVDA", subtype="bearish")
    _out(db_session, e2, excess=-0.04)   # bearish + negative = hit, but raw value is -0.04
    db_session.commit()

    stats = compute_hit_rate(db_session, horizon=5)
    # Both hit, but raw mean is (0.03 + -0.04) / 2 = -0.005
    assert stats.avg_excess_return == pytest.approx(-0.005)


def test_get_per_ticker_hit_rates_orders_by_hit_rate_desc(db_session):
    from marketpulse.evaluation.scoring import get_per_ticker_hit_rates

    # AAPL: 2/2 hits
    for _ in range(2):
        e = _ev(db_session, ticker="AAPL", subtype="bullish")
        _out(db_session, e, excess=0.03)
    # NVDA: 1/2 hits
    e = _ev(db_session, ticker="NVDA", subtype="bullish")
    _out(db_session, e, excess=0.03)
    e = _ev(db_session, ticker="NVDA", subtype="bullish")
    _out(db_session, e, excess=-0.02)
    db_session.commit()

    rows = get_per_ticker_hit_rates(db_session, horizon=5)
    assert [r.ticker for r in rows] == ["AAPL", "NVDA"]
    assert rows[0].hit_rate == pytest.approx(1.0)
    assert rows[1].hit_rate == pytest.approx(0.5)


def test_get_per_ticker_hit_rates_excludes_zero_n(db_session):
    """Tickers with no events at this horizon don't appear."""
    from marketpulse.evaluation.scoring import get_per_ticker_hit_rates

    _ev(db_session, ticker="AAPL")   # no outcome
    db_session.commit()

    rows = get_per_ticker_hit_rates(db_session, horizon=5)
    assert rows == []


def test_get_hit_rate_trend_returns_window_days_entries(db_session):
    from marketpulse.evaluation.scoring import get_hit_rate_trend

    # 30 days of 1 event/day, all bullish-hits
    for d in range(30):
        e = _ev(db_session, days_ago=d, subtype="bullish")
        _out(db_session, e, excess=0.03)
    db_session.commit()

    trend = get_hit_rate_trend(db_session, horizon=5, window_days=30, rolling=10)
    # 30 days in window
    assert len(trend) == 30
    # Each rolling 10-day window contains all hits → hit_rate = 1.0
    assert all(d.hit_rate == pytest.approx(1.0) for d in trend if d.n_total > 0)


def test_get_recent_events_with_outcomes_limit_and_order(db_session):
    from marketpulse.evaluation.scoring import get_recent_events_with_outcomes

    # 5 events at varying days_ago
    for d in (1, 5, 10, 20, 30):
        e = _ev(db_session, days_ago=d, ticker=f"T{d}")
        _out(db_session, e, excess=0.03)
    db_session.commit()

    rows = get_recent_events_with_outcomes(db_session, horizon=5, limit=3)
    assert len(rows) == 3
    # Newest first: days_ago=1 → T1, days_ago=5 → T5, days_ago=10 → T10
    assert [r.ticker for r in rows] == ["T1", "T5", "T10"]


def test_compute_hit_rate_filters_by_strategy(db_session):
    from marketpulse.evaluation.scoring import compute_hit_rate

    # 2 events: one tagged momentum_breakout, one tagged general
    e1 = _ev(db_session, ticker="AAA", subtype="bullish")
    e1.payload = {**e1.payload, "strategy": "momentum_breakout"}
    _out(db_session, e1, excess=0.03)

    e2 = _ev(db_session, ticker="BBB", subtype="bullish")
    e2.payload = {**e2.payload, "strategy": "general"}
    _out(db_session, e2, excess=0.03)
    db_session.commit()

    stats_mb = compute_hit_rate(db_session, horizon=5, strategy="momentum_breakout")
    assert stats_mb.n_total == 1

    stats_gen = compute_hit_rate(db_session, horizon=5, strategy="general")
    assert stats_gen.n_total == 1


def test_compute_hit_rate_strategy_none_preserves_phase_2_behavior(db_session):
    """strategy=None (default) does NOT filter — includes events with no strategy field."""
    from marketpulse.evaluation.scoring import compute_hit_rate

    # One event with strategy, one without (Phase 2 style)
    e1 = _ev(db_session, ticker="AAA")
    e1.payload = {**e1.payload, "strategy": "momentum_breakout"}
    _out(db_session, e1, excess=0.03)

    e2 = _ev(db_session, ticker="BBB")  # no strategy in payload
    _out(db_session, e2, excess=0.03)
    db_session.commit()

    stats = compute_hit_rate(db_session, horizon=5)  # strategy default None
    assert stats.n_total == 2  # both counted


def test_get_per_ticker_hit_rates_filters_by_strategy(db_session):
    from marketpulse.evaluation.scoring import get_per_ticker_hit_rates

    e1 = _ev(db_session, ticker="AAA", subtype="bullish")
    e1.payload = {**e1.payload, "strategy": "momentum_breakout"}
    _out(db_session, e1, excess=0.03)

    e2 = _ev(db_session, ticker="BBB", subtype="bullish")
    e2.payload = {**e2.payload, "strategy": "fundamental_value"}
    _out(db_session, e2, excess=0.03)
    db_session.commit()

    rows = get_per_ticker_hit_rates(db_session, horizon=5, strategy="momentum_breakout")
    assert [r.ticker for r in rows] == ["AAA"]


def test_get_hit_rate_trend_filters_by_strategy(db_session):
    from marketpulse.evaluation.scoring import get_hit_rate_trend

    for d in range(30):
        e = _ev(db_session, days_ago=d, subtype="bullish")
        e.payload = {**e.payload, "strategy": "momentum_breakout"}
        _out(db_session, e, excess=0.03)
    db_session.commit()

    trend_mb = get_hit_rate_trend(db_session, horizon=5, window_days=30,
                                   rolling=10, strategy="momentum_breakout")
    trend_other = get_hit_rate_trend(db_session, horizon=5, window_days=30,
                                     rolling=10, strategy="fundamental_value")
    assert any(d.n_total > 0 for d in trend_mb)
    assert all(d.n_total == 0 for d in trend_other)


def test_get_recent_events_with_outcomes_filters_by_strategy(db_session):
    from marketpulse.evaluation.scoring import get_recent_events_with_outcomes

    e1 = _ev(db_session, ticker="AAA")
    e1.payload = {**e1.payload, "strategy": "momentum_breakout"}
    _out(db_session, e1, excess=0.03)
    e2 = _ev(db_session, ticker="BBB")
    e2.payload = {**e2.payload, "strategy": "general"}
    _out(db_session, e2, excess=0.03)
    db_session.commit()

    rows = get_recent_events_with_outcomes(db_session, horizon=5,
                                            strategy="momentum_breakout")
    assert [r.ticker for r in rows] == ["AAA"]
