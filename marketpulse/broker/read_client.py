"""Read-only broker client Protocol."""

from __future__ import annotations

from typing import Protocol

from marketpulse.broker.types import BrokerSnapshot


class BrokerReadClient(Protocol):
    def fetch_snapshot(self) -> BrokerSnapshot: ...
