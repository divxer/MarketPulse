"""Strategy YAML system — Phase 3.

A strategy is a named, versioned, YAML-defined playbook for /stock AI
analysis. The router picks one strategy per ticker; deep analysis runs
with that strategy's specialist instructions.
"""
from marketpulse.strategies.types import Strategy

__all__ = ["Strategy"]
