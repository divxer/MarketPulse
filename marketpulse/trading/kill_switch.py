"""Phase 6a kill switch — env-var force-on + DB persisted flag.

Precedence: env var True → always active. Otherwise read DB.
Flip writes a KILL_SWITCH_FLIPPED audit row through the repository
(single-writer surface). See lock 6a-L8 for the two-layer enforcement
contract (cycle-level skip in daily_cycle + per-order check inside
ForwardExecutionEngine.place_order).

Rule #3: KillSwitchState does NOT touch `repo._session` directly — it
reads via `repo.latest_kill_switch_state()`."""

from __future__ import annotations

import os
from datetime import datetime

from marketpulse.trading.repository import Repository
from marketpulse.trading.types import AuditEventType


class KillSwitchState:
    def __init__(self, *, env_var: str, repository: Repository) -> None:
        self._env_var = env_var
        self._repo = repository

    def _env_truthy(self) -> bool:
        v = os.environ.get(self._env_var, "")
        return v.lower() in ("1", "true", "yes", "on")

    def _db_state(self) -> bool:
        """Read latest KILL_SWITCH_FLIPPED via repository helper (lock iii)."""
        return self._repo.latest_kill_switch_state()

    def is_active(self) -> bool:
        if self._env_truthy():
            return True
        return self._db_state()

    def flip(
        self,
        *,
        new_state: bool,
        reason: str,
        actor: str,
        timestamp: datetime,
    ) -> None:
        prior = self._db_state()
        with self._repo.transaction():
            self._repo.write_audit_event(
                event_type=AuditEventType.KILL_SWITCH_FLIPPED,
                order_id=None,
                strategy=None,
                reason=reason,
                context={
                    "from_state": prior,
                    "to_state": new_state,
                    "actor": actor,
                },
                timestamp=timestamp,
            )
