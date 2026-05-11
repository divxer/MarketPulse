"""Anomaly / technical signal detection on a ticker's recent price history.

Signals returned by detect_signals():
  BIG_MOVE              — |change_pct| >= 5%
  VOLUME_SPIKE          — volume >= 2x 20-day average
  MA20_BREAKOUT         — close crosses above 20-day SMA
  EMA_GOLDEN_CROSS      — EMA12 crosses above EMA26 (bullish momentum shift)
  EMA_DEATH_CROSS       — EMA12 crosses below EMA26 (bearish momentum shift)
  RSI_OVERBOUGHT        — 14-period RSI >= 70
  RSI_OVERSOLD          — 14-period RSI <= 30
  BOLLINGER_UPPER       — close above upper Bollinger band (20, 2σ)
  BOLLINGER_LOWER       — close below lower Bollinger band (20, 2σ)
"""

from marketpulse.data.types import Bar, Quote

BIG_MOVE_PCT = 5.0
VOLUME_SPIKE_RATIO = 2.0

EMA_SHORT = 12
EMA_LONG = 26

RSI_PERIOD = 14
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0

BB_PERIOD = 20
BB_STD_DEV = 2.0


def _ema(values: list[float], period: int) -> list[float] | None:
    """Exponential moving average. Returns one EMA per input from index `period-1` onward,
    so output length = len(values) - period + 1. None if not enough data."""
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out = [seed]
    for v in values[period:]:
        out.append((v - out[-1]) * multiplier + out[-1])
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    """Sparse EMA — same length as input, leading Nones where window isn't filled.

    Public counterpart to `_ema` which returns a shorter array. Used by `macd()`
    and the chart-data endpoint where every series must align by index.
    """
    out: list[float | None] = []
    if len(values) < period:
        return [None] * len(values)
    multiplier = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out.extend([None] * (period - 1))
    out.append(seed)
    for v in values[period:]:
        prev = out[-1]
        assert prev is not None  # invariant: out[period-1:] is dense
        out.append((v - prev) * multiplier + prev)
    return out


def macd(
    values: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """MACD line, signal line, and histogram. Each is the same length as `values`,
    with leading Nones during indicator warm-up.

    line = EMA(fast) - EMA(slow)
    signal = EMA(line, signal)
    histogram = line - signal
    """
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    # MACD line is defined where slow EMA is.
    line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_ema, slow_ema, strict=True)
    ]
    # Signal line is EMA of the dense tail of line.
    dense_tail = [v for v in line if v is not None]
    if len(dense_tail) >= signal:
        signal_tail = ema(dense_tail, signal)
        # Re-pad with Nones for positions where line itself was None.
        pad = len(line) - len(signal_tail)
        signal_line: list[float | None] = [None] * pad + signal_tail
    else:
        signal_line = [None] * len(line)
    hist = [
        (l - s) if (l is not None and s is not None) else None
        for l, s in zip(line, signal_line, strict=True)
    ]
    return line, signal_line, hist


def sma(values: list[float], period: int) -> list[float | None]:
    """Simple moving average. Returns one entry per input position.
    Positions where the window isn't yet filled (< period values seen)
    return None, matching the lightweight-charts sparse-series convention.
    """
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(sum(values[i - period + 1 : i + 1]) / period)
    return out


def _ema_cross(bars: list[Bar]) -> str | None:
    """Detect EMA12/EMA26 cross on the latest bar. Returns 'GOLDEN', 'DEATH', or None."""
    if len(bars) < EMA_LONG + 1:
        return None
    closes = [b.close for b in bars]
    short = _ema(closes, EMA_SHORT)
    long_ = _ema(closes, EMA_LONG)
    if not short or not long_:
        return None
    # Align tails — long has fewer entries (starts later).
    n = min(len(short), len(long_))
    if n < 2:
        return None
    s = short[-n:]
    l_ = long_[-n:]
    prev = s[-2] - l_[-2]
    curr = s[-1] - l_[-1]
    if prev <= 0 < curr:
        return "GOLDEN"
    if prev >= 0 > curr:
        return "DEATH"
    return None


def _rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """Wilder's RSI. Returns 0-100 or None if not enough data."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_gain == 0 and avg_loss == 0:
        return None  # no movement → RSI undefined, skip the signal
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def _bollinger(
    closes: list[float], period: int = BB_PERIOD, num_std: float = BB_STD_DEV
) -> tuple[float, float, float] | None:
    """Returns (upper, middle/SMA, lower) bands or None."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    sma = sum(window) / period
    var = sum((x - sma) ** 2 for x in window) / period
    std = var**0.5
    return sma + num_std * std, sma, sma - num_std * std


def detect_signals(quote: Quote, bars: list[Bar]) -> list[str]:
    signals: list[str] = []
    if abs(quote.change_pct) >= BIG_MOVE_PCT:
        signals.append("BIG_MOVE")
    if quote.avg_volume_20d > 0 and quote.volume >= VOLUME_SPIKE_RATIO * quote.avg_volume_20d:
        signals.append("VOLUME_SPIKE")
    if len(bars) >= 21:
        ma20 = sum(b.close for b in bars[-21:-1]) / 20
        prev_close = bars[-2].close
        last_close = bars[-1].close
        if prev_close <= ma20 < last_close:
            signals.append("MA20_BREAKOUT")

    closes = [b.close for b in bars]

    cross = _ema_cross(bars)
    if cross == "GOLDEN":
        signals.append("EMA_GOLDEN_CROSS")
    elif cross == "DEATH":
        signals.append("EMA_DEATH_CROSS")

    rsi = _rsi(closes)
    if rsi is not None:
        if rsi >= RSI_OVERBOUGHT:
            signals.append("RSI_OVERBOUGHT")
        elif rsi <= RSI_OVERSOLD:
            signals.append("RSI_OVERSOLD")

    band = _bollinger(closes)
    if band and closes:
        upper, _, lower = band
        if upper > lower + 1e-9:  # skip degenerate zero-variance bands
            last_close = closes[-1]
            if last_close > upper:
                signals.append("BOLLINGER_UPPER")
            elif last_close < lower:
                signals.append("BOLLINGER_LOWER")

    return signals


def scan_signal_markers(bars: list[Bar]) -> list[dict[str, str]]:
    """Walk the series and emit one marker per (signal_type, first-fire) pair.

    A signal is considered "firing" at bar i if it would have been emitted by
    detect_signals() on the prefix bars[:i+1]. Once a signal type fires, it is
    deduplicated until it stops firing for at least one bar (so a sustained
    overbought RSI gets one marker on entry, not one per bar).
    """
    if not bars:
        return []

    markers: list[dict[str, str]] = []
    # For each signal type, was it firing on the previous bar?
    previously_firing: dict[str, bool] = {}

    closes = [b.close for b in bars]

    def _add(i: int, signal_type: str, note: str) -> None:
        was_firing = previously_firing.get(signal_type, False)
        if not was_firing:
            markers.append({
                "time": bars[i].date.isoformat(),
                "type": signal_type,
                "note": note,
            })
        previously_firing[signal_type] = True

    def _clear(signal_type: str) -> None:
        previously_firing[signal_type] = False

    # Indicators precomputed over the full series for efficient lookup.
    ema12 = ema(closes, EMA_SHORT)
    ema26 = ema(closes, EMA_LONG)

    for i in range(len(bars)):
        prefix_closes = closes[: i + 1]

        # EMA cross — use precomputed series and look at index i vs i-1
        if i >= 1 and ema12[i] is not None and ema26[i] is not None \
                and ema12[i - 1] is not None and ema26[i - 1] is not None:
            prev_diff = ema12[i - 1] - ema26[i - 1]
            curr_diff = ema12[i] - ema26[i]
            if prev_diff <= 0 < curr_diff:
                _add(i, "ema_golden_cross",
                     f"EMA12 (${ema12[i]:.2f}) crossed above EMA26 (${ema26[i]:.2f})")
                _clear("ema_death_cross")
            elif prev_diff >= 0 > curr_diff:
                _add(i, "ema_death_cross",
                     f"EMA12 (${ema12[i]:.2f}) crossed below EMA26 (${ema26[i]:.2f})")
                _clear("ema_golden_cross")

        # RSI overbought / oversold (need full _rsi to handle Wilder's smoothing)
        rsi_val = _rsi(prefix_closes)
        if rsi_val is not None:
            if rsi_val >= RSI_OVERBOUGHT:
                _add(i, "rsi_overbought", f"RSI(14) = {rsi_val:.1f} (≥ {RSI_OVERBOUGHT:.0f})")
                _clear("rsi_oversold")
            elif rsi_val <= RSI_OVERSOLD:
                _add(i, "rsi_oversold", f"RSI(14) = {rsi_val:.1f} (≤ {RSI_OVERSOLD:.0f})")
                _clear("rsi_overbought")
            else:
                _clear("rsi_overbought")
                _clear("rsi_oversold")

        # Bollinger band touch
        band = _bollinger(prefix_closes)
        if band:
            upper, _, lower = band
            last_close = prefix_closes[-1]
            if upper > lower + 1e-9:
                if last_close > upper:
                    _add(i, "bollinger_upper",
                         f"Close ${last_close:.2f} above upper band ${upper:.2f}")
                    _clear("bollinger_lower")
                elif last_close < lower:
                    _add(i, "bollinger_lower",
                         f"Close ${last_close:.2f} below lower band ${lower:.2f}")
                    _clear("bollinger_upper")
                else:
                    _clear("bollinger_upper")
                    _clear("bollinger_lower")

    return markers
