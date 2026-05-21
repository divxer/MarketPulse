"""Phase 5e: System policy constants (control plane).

Per spec § 2 lock #7, this module is the single home for system-policy
constants that should NOT live in signal modules (control plane vs data
plane separation). The constants here may be referenced as provenance
comments at relevant call sites, but per spec § 2 lock #21 they are
NEVER branched on at runtime in v0. Future variant dispatch would arrive
with the v2 spec, not as retrofitted branches in this code.
"""
from __future__ import annotations

MIN_OVERLAP_DAYS: int = 30
"""Minimum days of pool-return overlap required before pool_corr is
computed. Below this threshold, pool_corr_excluding_self returns None.

Spec § 2 lock #7. Phase 5d originally hardcoded this as a magic number
at the WEIGHT step call site; Phase 5e promotes it to a module-level
constant for legibility and to anchor it as a system-policy decision.
"""
