# Layer: invariant
"""6a-1: ExecutionEngine Protocol has EXACTLY 3 methods (CQRS boundary)."""

from __future__ import annotations


def test_protocol_has_exactly_three_methods():
    from marketpulse.trading.execution_engine import ExecutionEngine

    # Use __dict__ instead of dir() — typing.Protocol's dir() includes
    # inherited internals (e.g. __subclasshook__, __init_subclass__,
    # _is_protocol, _is_runtime_protocol) that can shift between Python
    # versions. __dict__ contains only methods declared on this class.
    own_methods = {
        k for k, v in ExecutionEngine.__dict__.items()
        if callable(v) and not k.startswith("_")
    }
    assert own_methods == {"place_order", "cancel_order", "tick"}, (
        f"ExecutionEngine Protocol drift; got {own_methods}"
    )
