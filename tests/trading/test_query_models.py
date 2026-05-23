"""Tests for Phase 6f paper-trading read-side query models."""

from __future__ import annotations

import pytest


def test_section_ok_requires_non_none_data():
    from marketpulse.trading.query_models import section_ok

    with pytest.raises(ValueError, match="ok SectionResult requires non-None data"):
        section_ok(None)


def test_section_error_requires_none_data():
    from marketpulse.trading.query_models import SectionResult

    with pytest.raises(ValueError, match="error SectionResult requires data=None"):
        SectionResult(status="error", data=[])


def test_section_error_sets_degraded_reason():
    from marketpulse.trading.query_models import section_error

    result = section_error("Unable to load Positions", "positions query failed")

    assert result.status == "error"
    assert result.data is None
    assert result.error_title == "Unable to load Positions"
    assert result.degraded_reason == "positions query failed"


def test_fresh_db_dashboard_is_healthy_empty_state(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Healthy"
    assert dashboard.current_operational_window.started_at is None
    assert dashboard.current_operational_window.source_event_type is None
    assert (
        dashboard.current_operational_window.label
        == "No paper tick has completed yet"
    )
    assert dashboard.critical_events.status == "ok"
    assert dashboard.critical_events.data == []
    assert (
        dashboard.critical_events.empty_message
        == "No operational events in current cycle"
    )
    assert dashboard.positions.status == "ok"
    assert dashboard.positions.data == []
    assert dashboard.positions.empty_message == "No open paper positions"
    assert dashboard.order_lifecycles.status == "ok"
    assert dashboard.order_lifecycles.data == []
    assert (
        dashboard.order_lifecycles.empty_message
        == "No order lifecycle activity in current cycle"
    )
