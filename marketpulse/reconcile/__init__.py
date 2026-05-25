"""Phase 7c - broker-vs-paper position reconciliation.

Pure read-only computation: no DB writes, no migration, no scheduler hook.
Architecture guard at tests/architecture/test_lab_reconcile_isolation.py
enforces the read-only boundary.
"""
