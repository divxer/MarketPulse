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
