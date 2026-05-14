"""Evaluation framework: events, outcomes, forward-return computation.

Public API:
    record_event() — write an event
    compute_outcomes_for_pending_events() — nightly outcome computation
    forward_return_at_horizon() — pure math helper (Phases 2/3 may call)
"""
from marketpulse.evaluation.benchmark import (
    BENCHMARK_TICKER,
    benchmark_forward_return,
)
from marketpulse.evaluation.constants import (
    AIVerdict,
    EventType,
    SignalType,
)
from marketpulse.evaluation.events import record_event
from marketpulse.evaluation.forward_return import (
    ForwardReturnResult,
    forward_return_at_horizon,
)
from marketpulse.evaluation.outcomes import (
    DEFAULT_HORIZONS,
    ComputeOutcomeReport,
    compute_outcomes_for_pending_events,
)

__all__ = [
    "AIVerdict",
    "BENCHMARK_TICKER",
    "ComputeOutcomeReport",
    "DEFAULT_HORIZONS",
    "EventType",
    "ForwardReturnResult",
    "SignalType",
    "benchmark_forward_return",
    "compute_outcomes_for_pending_events",
    "forward_return_at_horizon",
    "record_event",
]
