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


def _quiet_quote() -> Quote:
    return Quote(ticker="X", price=100, change_pct=0.5, volume=900_000,
                 avg_volume_20d=1_000_000, fetched_at=date(2026, 5, 7))  # type: ignore[arg-type]


def _bars_from_closes(closes: list[float]) -> list[Bar]:
    return [
        Bar(date=date(2026, 1, 1), open=c, high=c, low=c, close=c, volume=1_000_000)
        for c in closes
    ]


def test_ema_golden_cross() -> None:
    # 26 flat bars seed both EMAs at 100; the 27th bar jumps to 150 so EMA12
    # (faster) ends above EMA26 (slower), triggering golden cross on last bar.
    closes = [100.0] * 26 + [150.0]
    sigs = detect_signals(_quiet_quote(), _bars_from_closes(closes))
    assert "EMA_GOLDEN_CROSS" in sigs


def test_ema_death_cross() -> None:
    # Mirror image: a sharp drop after a flat history → EMA12 dives below EMA26.
    closes = [100.0] * 26 + [50.0]
    sigs = detect_signals(_quiet_quote(), _bars_from_closes(closes))
    assert "EMA_DEATH_CROSS" in sigs


def test_rsi_overbought() -> None:
    # Pure uptrend → RSI saturates at 100, well above 70
    closes = [100 + i for i in range(20)]
    sigs = detect_signals(_quiet_quote(), _bars_from_closes(closes))
    assert "RSI_OVERBOUGHT" in sigs


def test_rsi_oversold() -> None:
    # Pure downtrend → RSI saturates near 0, well below 30
    closes = [100 - i for i in range(20)]
    sigs = detect_signals(_quiet_quote(), _bars_from_closes(closes))
    assert "RSI_OVERSOLD" in sigs


def test_bollinger_upper() -> None:
    # 19 days flat at 100, then sharp spike to 130 → close way above upper band
    closes = [100.0] * 19 + [130.0]
    sigs = detect_signals(_quiet_quote(), _bars_from_closes(closes))
    assert "BOLLINGER_UPPER" in sigs


def test_bollinger_lower() -> None:
    # 19 days flat at 100, then drop to 70 → close way below lower band
    closes = [100.0] * 19 + [70.0]
    sigs = detect_signals(_quiet_quote(), _bars_from_closes(closes))
    assert "BOLLINGER_LOWER" in sigs


def test_no_advanced_signals_on_flat_series() -> None:
    closes = [100.0] * 30
    sigs = detect_signals(_quiet_quote(), _bars_from_closes(closes))
    # None of the new signals should fire on a perfectly flat series.
    for s in ("EMA_GOLDEN_CROSS", "EMA_DEATH_CROSS",
              "RSI_OVERBOUGHT", "RSI_OVERSOLD",
              "BOLLINGER_UPPER", "BOLLINGER_LOWER"):
        assert s not in sigs
