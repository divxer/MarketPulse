from marketpulse.evaluation.constants import (
    AIVerdict,
    EventType,
    SignalType,
)


def test_ai_verdict_has_exactly_three_values():
    assert AIVerdict.all() == {"bullish", "neutral", "bearish"}


def test_signal_type_has_exactly_six_values():
    assert SignalType.all() == {
        "ema_golden_cross", "ema_death_cross",
        "rsi_overbought", "rsi_oversold",
        "bollinger_upper", "bollinger_lower",
    }


def test_signal_type_matches_recap_signals_emitter():
    """Regression: keep this taxonomy in sync with what signals.py emits.

    If scan_signal_markers adds a new signal type, this test fails and
    forces us to add it to SignalType (and consider Phase 3 implications).
    """
    import inspect

    from marketpulse.recap.signals import scan_signal_markers
    source = inspect.getsource(scan_signal_markers)
    # Every SignalType constant must appear as a string literal in the source
    for type_name in SignalType.all():
        assert f'"{type_name}"' in source, (
            f"{type_name} not emitted by scan_signal_markers — taxonomy drift"
        )


def test_event_type_subtypes_map_complete():
    assert EventType.all() == {"ai_analysis", "signal_marker"}
    assert EventType.SUBTYPES[EventType.AI_ANALYSIS]() == AIVerdict.all()
    assert EventType.SUBTYPES[EventType.SIGNAL_MARKER]() == SignalType.all()
