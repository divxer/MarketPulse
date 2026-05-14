"""Standardized taxonomy for evaluation events.

record_event() validates against these — no free-form subtype strings.
Mirrors marketpulse.recap.signals taxonomy for signal_marker; defines
canonical labels for ai_analysis.
"""


class AIVerdict:
    """Claude analysis verdict labels."""
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"

    @classmethod
    def all(cls) -> set[str]:
        return {cls.BULLISH, cls.NEUTRAL, cls.BEARISH}


class SignalType:
    """K-line marker types — mirrors marketpulse.recap.signals."""
    EMA_GOLDEN_CROSS = "ema_golden_cross"
    EMA_DEATH_CROSS = "ema_death_cross"
    RSI_OVERBOUGHT = "rsi_overbought"
    RSI_OVERSOLD = "rsi_oversold"
    BOLLINGER_UPPER = "bollinger_upper"
    BOLLINGER_LOWER = "bollinger_lower"

    @classmethod
    def all(cls) -> set[str]:
        return {
            cls.EMA_GOLDEN_CROSS, cls.EMA_DEATH_CROSS,
            cls.RSI_OVERBOUGHT, cls.RSI_OVERSOLD,
            cls.BOLLINGER_UPPER, cls.BOLLINGER_LOWER,
        }


class EventType:
    """Top-level event partition."""
    AI_ANALYSIS = "ai_analysis"
    SIGNAL_MARKER = "signal_marker"

    SUBTYPES = {
        AI_ANALYSIS: AIVerdict.all,
        SIGNAL_MARKER: SignalType.all,
    }

    @classmethod
    def all(cls) -> set[str]:
        return set(cls.SUBTYPES.keys())
