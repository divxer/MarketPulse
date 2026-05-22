"""Phase 6g observability and alerting for paper-trading audit events.

This package is a strict read-side consumer of paper_audit_event. It writes no
trading state and dispatches notifications through marketpulse.alerts.notifier.
"""
