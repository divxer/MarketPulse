from datetime import date

from marketpulse.data.types import Bar, Quote
from marketpulse.recap.signals import detect_signals


def _bar(d: int, close: float, volume: int = 1_000_000) -> Bar:
    return Bar(date=date(2026, 5, d), open=close, high=close, low=close,
               close=close, volume=volume)


def test_big_move_signal() -> None:
    quote = Quote(ticker="X", price=110, change_pct=6.0, volume=1_000_000,
                  avg_volume_20d=1_000_000, fetched_at=date(2026, 5, 7))  # type: ignore[arg-type]
    bars = [_bar(i, 100) for i in range(1, 21)]
    sigs = detect_signals(quote, bars)
    assert "BIG_MOVE" in sigs


def test_volume_spike_signal() -> None:
    quote = Quote(ticker="X", price=100, change_pct=0.5, volume=3_000_000,
                  avg_volume_20d=1_000_000, fetched_at=date(2026, 5, 7))  # type: ignore[arg-type]
    bars = [_bar(i, 100) for i in range(1, 21)]
    sigs = detect_signals(quote, bars)
    assert "VOLUME_SPIKE" in sigs


def test_ma20_breakout_signal() -> None:
    bars = [_bar(i, 100) for i in range(1, 21)]
    bars.append(_bar(22, 110))  # last close above MA20=100
    quote = Quote(ticker="X", price=110, change_pct=1.0, volume=1_000_000,
                  avg_volume_20d=1_000_000, fetched_at=date(2026, 5, 7))  # type: ignore[arg-type]
    sigs = detect_signals(quote, bars)
    assert "MA20_BREAKOUT" in sigs


def test_no_signals_when_quiet() -> None:
    bars = [_bar(i, 100) for i in range(1, 21)]
    quote = Quote(ticker="X", price=100.5, change_pct=0.5, volume=900_000,
                  avg_volume_20d=1_000_000, fetched_at=date(2026, 5, 7))  # type: ignore[arg-type]
    sigs = detect_signals(quote, bars)
    assert sigs == []
