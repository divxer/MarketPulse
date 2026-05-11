from datetime import date, timedelta

from marketpulse.data.types import Bar, Quote
from marketpulse.recap.signals import (
    bollinger_series,
    detect_signals,
    ema,
    macd,
    rsi_series,
    scan_signal_markers,
    sma,
)


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


def test_sma_basic() -> None:
    # SMA with period=3 over [1,2,3,4,5] → [None, None, 2.0, 3.0, 4.0]
    out = sma([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    assert out == [None, None, 2.0, 3.0, 4.0]


def test_sma_period_longer_than_input() -> None:
    assert sma([1.0, 2.0], 5) == [None, None]


def test_sma_period_one_returns_input() -> None:
    assert sma([1.0, 2.0, 3.0], 1) == [1.0, 2.0, 3.0]


def test_sma_empty_input() -> None:
    assert sma([], 5) == []


def test_ema_sparse_returns_input_length_with_leading_nones() -> None:
    # Period 3: first 2 entries are None, then EMAs
    out = ema([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    assert len(out) == 5
    assert out[0] is None
    assert out[1] is None
    # Seed = simple mean of first 3 = 2.0
    assert out[2] == 2.0
    # multiplier = 2/(3+1) = 0.5; out[3] = (4 - 2) * 0.5 + 2 = 3.0
    assert out[3] == 3.0
    # out[4] = (5 - 3) * 0.5 + 3 = 4.0
    assert out[4] == 4.0


def test_ema_too_short_all_nones() -> None:
    assert ema([1.0, 2.0], 5) == [None, None]


def test_macd_basic_shape() -> None:
    # 50 ascending values → all three series should compute past warm-up
    values = [float(i) for i in range(1, 51)]
    line, signal, hist = macd(values, fast=12, slow=26, signal=9)
    assert len(line) == 50
    assert len(signal) == 50
    assert len(hist) == 50
    # First slow-1=25 entries of line are None (need full slow EMA)
    assert line[24] is None
    assert line[25] is not None
    # Signal line warm-up: slow-1 + (signal-1) = 25 + 8 = 33
    assert signal[32] is None
    assert signal[33] is not None
    # Histogram = line - signal where both are non-None
    assert hist[40] == line[40] - signal[40]


def test_macd_too_short_all_nones() -> None:
    values = [1.0, 2.0, 3.0]
    line, signal, hist = macd(values)
    assert all(v is None for v in line)
    assert all(v is None for v in signal)
    assert all(v is None for v in hist)


def _bar_dated(d: date, close: float, vol: int = 1_000_000) -> Bar:
    return Bar(date=d, open=close, high=close, low=close, close=close, volume=vol)


def test_scan_signal_markers_detects_ema_golden_cross() -> None:
    # Construct a series where EMA12 crosses above EMA26 once.
    # Long downtrend then sharp recovery → guaranteed cross.
    today = date.today()
    closes = list(range(100, 50, -1)) + list(range(50, 100))  # 100 bars
    bars = [_bar_dated(today - timedelta(days=len(closes) - i), float(c))
            for i, c in enumerate(closes)]
    markers = scan_signal_markers(bars)
    types = [m["type"] for m in markers]
    assert "ema_golden_cross" in types


def test_scan_signal_markers_emits_once_not_per_bar() -> None:
    # If RSI stays >= 70 for many bars, we want one marker at the first cross,
    # not one marker per bar.
    today = date.today()
    closes = [10.0] * 30 + list(range(10, 60))
    bars = [_bar_dated(today - timedelta(days=len(closes) - i), float(c))
            for i, c in enumerate(closes)]
    markers = scan_signal_markers(bars)
    overbought = [m for m in markers if m["type"] == "rsi_overbought"]
    # The rising series saturates RSI well above 70 → must fire EXACTLY once
    # (sustained overbought is deduplicated by the marker scanner).
    assert len(overbought) == 1


def test_scan_signal_markers_empty_series() -> None:
    assert scan_signal_markers([]) == []


def test_scan_signal_markers_each_marker_has_required_fields() -> None:
    today = date.today()
    closes = list(range(100, 50, -1)) + list(range(50, 100))
    bars = [_bar_dated(today - timedelta(days=len(closes) - i), float(c))
            for i, c in enumerate(closes)]
    markers = scan_signal_markers(bars)
    for m in markers:
        assert set(m.keys()) >= {"time", "type", "note"}
        assert isinstance(m["time"], str)  # ISO date for JSON
        assert isinstance(m["type"], str)
        assert isinstance(m["note"], str)


def test_rsi_series_matches_private_rsi_on_full_input() -> None:
    # Series result at index len-1 should equal _rsi() over the full closes.
    from marketpulse.recap.signals import _rsi
    closes = [100.0 + (i % 7 - 3) for i in range(50)]
    series = rsi_series(closes)
    assert len(series) == len(closes)
    assert series[0] is None
    # Warmup: first non-None at index `period` (=14 by default)
    assert series[14] is not None
    assert series[-1] == _rsi(closes)


def test_rsi_series_too_short() -> None:
    assert rsi_series([1.0, 2.0]) == [None, None]


def test_bollinger_series_basic() -> None:
    closes = [100.0] * 20 + [130.0]
    upper, middle, lower = bollinger_series(closes, period=20, num_std=2.0)
    assert len(upper) == len(middle) == len(lower) == 21
    # First 19 entries are None (window not filled)
    assert all(x is None for x in upper[:19])
    # Index 19: window is 20 flat 100s → std=0 → upper = lower = middle = 100
    assert middle[19] == 100.0
    assert upper[19] == 100.0
    assert lower[19] == 100.0
    # Index 20: window has one 130 + nineteen 100s → mean=101.5, std>0
    assert middle[20] is not None and middle[20] > 100
    assert upper[20] is not None and upper[20] > middle[20]
    assert lower[20] is not None and lower[20] < middle[20]


def test_bollinger_series_too_short() -> None:
    upper, middle, lower = bollinger_series([1.0, 2.0], period=20)
    assert upper == middle == lower == [None, None]
