"""Phase 5e: System policy constants (control plane).

Per spec § 2 lock #7, this module is the single home for system-policy
constants that should NOT live in signal modules (control plane vs data
plane separation). The constants here may be referenced as provenance
comments at relevant call sites, but per spec § 2 lock #21 they are
NEVER branched on at runtime in v0. Future variant dispatch would arrive
with the v2 spec, not as retrofitted branches in this code.
"""
from __future__ import annotations

from typing import Literal

MIN_OVERLAP_DAYS: int = 30
"""Minimum days of pool-return overlap required before pool_corr is
computed. Below this threshold, pool_corr_excluding_self returns None.

Spec § 2 lock #7. Phase 5d originally hardcoded this as a magic number
at the WEIGHT step call site; Phase 5e promotes it to a module-level
constant for legibility and to anchor it as a system-policy decision.
"""

POOL_CORR_MODE: Literal["LOO_ONLY_v0"] = "LOO_ONLY_v0"
"""Discriminator for the pool-correlation computation variant.

Spec § 2 lock #7 + #21. v0 ships LOO (leave-one-out via subtraction)
as the only mode. The constant is DOCUMENTARY-ONLY in v0 — no function
reads it, no test branches on it (beyond anchoring its value via the
test in test_backtest_policy.py). A future v2 non-LOO variant (e.g.,
counterfactual A-less simulation) would version-bump this constant to
e.g. 'LOO_OR_CF_v1' and add dispatch logic at THAT time, not as
retrofitted branches in v0 code.

This separation prevents the smell where a constant accumulates implicit
semantic meaning across phases without ever being exercised. v0 stays
pure; v2 adds dispatch as new code.
"""
