"""Single canonical audit-JSON normalizer (lock 6b-L17).

Every audit-writing code path (forward_engine._dump, CompositeRiskGate's
per_gate emission, future 6f UI render, 6g recap jobs, Phase 7 broker
audit) MUST route through `normalize_for_json`. No per-module
normalizers, no inline `json.dumps(asdict(...))` — that path either
crashes on Decimal or emits non-deterministic floats, both unacceptable
for the append-only audit ledger.

The function is intentionally narrow in scope: take any nested Python
object, return a JSON-safe structure (str, int, float, bool, None, list,
dict).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

__all__ = ["normalize_for_json"]


def normalize_for_json(value: Any) -> Any:
    """Recursively convert a value into a JSON-safe representation.

    Conversions:
      - Decimal → str (exact precision preserved)
      - datetime / date / time → .isoformat()
      - dataclass instance → dict (with same recursion applied to fields)
      - tuple → list (JSON has no tuples)
      - Mapping (incl. MappingProxyType) → dict (recursing on values)
      - list → list (recursing on elements)
      - str / int / float / bool / None → unchanged

    Non-trivial objects without a known conversion fall through unchanged.
    json.dumps will raise on them — that's the right failure mode (better
    than silent str() coercion that loses semantics).
    """
    if isinstance(value, Decimal):
        return str(value)
    # datetime IS a date subclass; check datetime first so we get the
    # full ISO format including time + tz.
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: normalize_for_json(getattr(value, f.name))
            for f in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {k: normalize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_for_json(v) for v in value]
    return value
