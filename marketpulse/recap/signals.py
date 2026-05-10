from marketpulse.data.types import Bar, Quote

BIG_MOVE_PCT = 5.0
VOLUME_SPIKE_RATIO = 2.0


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
    return signals
