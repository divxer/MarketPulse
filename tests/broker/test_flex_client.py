"""Phase 7a-Flex FlexClient tests."""
# Layer: unit

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from marketpulse.broker.flex_client import (
    FlexAuthError,
    FlexClient,
    FlexHttpError,
    FlexParseError,
    FlexReportTimeoutError,
    FlexSendRequestError,
    FlexStatementError,
)
from marketpulse.broker.types import BrokerSnapshot

FIXTURES = Path(__file__).parent / "fixtures" / "flex"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _mock_transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    """Return MockTransport that serves the given responses in order, then
    raises on extra calls."""
    iterator = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            return next(iterator)
        except StopIteration as exc:
            raise AssertionError(f"unexpected extra call to {request.url}") from exc

    return httpx.MockTransport(handler)


class TestSendRequest:
    def test_happy_path_returns_reference_code(self):
        transport = _mock_transport(
            [
                httpx.Response(200, content=_fixture("send_request_success.xml")),
            ]
        )
        client = FlexClient(token="t", query_id=123, transport=transport)
        ref = client._send_request()
        assert ref == "1234567890"

    def test_xml_error_code_raises_send_request_error(self):
        transport = _mock_transport(
            [
                httpx.Response(200, content=_fixture("err_send_request_bad_query.xml")),
            ]
        )
        client = FlexClient(token="t", query_id=999, transport=transport)
        with pytest.raises(FlexSendRequestError, match="1019"):
            client._send_request()

    def test_xml_auth_error_raises_auth_error(self):
        transport = _mock_transport(
            [
                httpx.Response(200, content=_fixture("err_auth.xml")),
            ]
        )
        client = FlexClient(token="bad", query_id=1, transport=transport)
        with pytest.raises(FlexAuthError):
            client._send_request()

    def test_5xx_raises_http_error(self):
        transport = _mock_transport([httpx.Response(503, content=b"Service Unavailable")])
        client = FlexClient(token="t", query_id=1, transport=transport)
        with pytest.raises(FlexHttpError, match="503"):
            client._send_request()

    def test_connect_timeout_raises_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("connect timed out")

        client = FlexClient(token="t", query_id=1, transport=httpx.MockTransport(handler))
        with pytest.raises(FlexHttpError, match="ConnectTimeout"):
            client._send_request()


class TestGetStatementPolling:
    def test_first_poll_ready(self):
        transport = _mock_transport(
            [
                httpx.Response(200, content=_fixture("send_request_success.xml")),
                httpx.Response(200, content=_fixture("full_paper.xml")),
            ]
        )
        client = FlexClient(
            token="t",
            query_id=1,
            transport=transport,
            poll_interval_seconds=0,
            max_wait_seconds=10,
        )
        xml = client._fetch_xml()
        assert b"AccountInformation" in xml

    def test_one_poll_in_progress_then_ready(self):
        transport = _mock_transport(
            [
                httpx.Response(200, content=_fixture("send_request_success.xml")),
                httpx.Response(200, content=_fixture("err_generation_in_progress.xml")),
                httpx.Response(200, content=_fixture("full_paper.xml")),
            ]
        )
        client = FlexClient(
            token="t",
            query_id=1,
            transport=transport,
            poll_interval_seconds=0,
            max_wait_seconds=10,
        )
        xml = client._fetch_xml()
        assert b"AccountInformation" in xml

    def test_timeout_raises_with_reference_code(self):
        in_progress = _fixture("err_generation_in_progress.xml")
        transport = _mock_transport(
            [
                httpx.Response(200, content=_fixture("send_request_success.xml")),
                *[httpx.Response(200, content=in_progress) for _ in range(20)],
            ]
        )
        client = FlexClient(
            token="t",
            query_id=1,
            transport=transport,
            poll_interval_seconds=0,
            max_wait_seconds=0,  # immediate timeout
        )
        with pytest.raises(FlexReportTimeoutError) as excinfo:
            client._fetch_xml()
        assert excinfo.value.reference_code == "1234567890"

    def test_statement_xml_error_after_ready_window(self):
        transport = _mock_transport(
            [
                httpx.Response(200, content=_fixture("send_request_success.xml")),
                httpx.Response(200, content=_fixture("err_report_expired.xml")),
            ]
        )
        client = FlexClient(
            token="t",
            query_id=1,
            transport=transport,
            poll_interval_seconds=0,
            max_wait_seconds=10,
        )
        with pytest.raises(FlexStatementError):
            client._fetch_xml()


class TestParser:
    def _parse(self, fixture_name: str, **kwargs) -> BrokerSnapshot:
        from marketpulse.broker.flex_client import FlexClient

        client = FlexClient(token="t", query_id=1, **kwargs)
        return client._parse_snapshot(_fixture(fixture_name))

    def test_full_paper_happy_path(self):
        snap = self._parse("full_paper.xml")
        assert snap.broker == "IBKR"
        assert snap.broker_environment == "paper"
        assert snap.account_id == "DU1234567"
        assert snap.account.base_currency == "USD"
        assert snap.account.net_liquidation == Decimal("100000.00")
        assert len(snap.cash) == 2
        assert {c.currency for c in snap.cash} == {"USD", "HKD"}
        assert len(snap.positions) == 3
        assert {p.symbol for p in snap.positions} == {"AAPL", "MSFT", "0700"}
        assert len(snap.executions) == 2
        assert snap.open_orders == ()  # Flex never produces open_orders (L18)

    def test_full_live_is_classified_live(self):
        snap = self._parse("full_live.xml")
        assert snap.broker_environment == "live"

    def test_account_only_returns_empty_tuples(self):
        snap = self._parse("account_only.xml")
        assert snap.account_id == "DU1234567"
        assert snap.cash == ()
        assert snap.positions == ()
        assert snap.executions == ()
        # When the EquitySummaryInBase section is absent, net_liquidation
        # must be None — operator forgot to tick the section in Flex Query.
        # (Activity Flex never exports buying_power / margin / excess liq.)
        assert snap.account.net_liquidation is None
        assert snap.account.buying_power is None
        assert snap.account.maintenance_margin is None
        assert snap.account.excess_liquidity is None

    def test_missing_account_section_raises(self):
        with pytest.raises(FlexParseError, match="Account"):
            self._parse("missing_account.xml")

    def test_missing_account_id_raises(self):
        with pytest.raises(FlexParseError, match="accountId"):
            self._parse("missing_account_id.xml")

    def test_malformed_xml_raises(self):
        with pytest.raises(FlexParseError):
            self._parse("malformed.xml")

    def test_multi_currency_all_parsed(self):
        snap = self._parse("multi_currency.xml")
        assert {c.currency for c in snap.cash} == {"USD", "HKD", "JPY", "CAD"}

    def test_multi_account_filtered_by_account_id(self):
        # When account_id filter set, only matching FlexStatement is used.
        snap = self._parse("multi_account.xml", account_id="DU1234567")
        assert snap.account_id == "DU1234567"

    def test_multi_account_no_filter_uses_first(self):
        snap = self._parse("multi_account.xml")
        # First statement wins; precise semantics documented in parser docstring
        assert snap.account_id.startswith("DU")

    def test_multi_account_no_filter_logs_warning(self, capsys):
        # structlog default config writes to stdout via PrintLogger; capsys
        # captures it. We assert the WARNING-level event key is emitted.
        snap = self._parse("multi_account.xml")
        out = capsys.readouterr().out
        assert snap.account_id.startswith("DU")
        assert "flex_multi_account_no_filter" in out

    def test_malformed_when_generated_logs_warning(self, capsys):
        from marketpulse.broker.flex_client import FlexClient

        result = FlexClient._parse_when_generated("not-a-date")
        out = capsys.readouterr().out
        # Fallback returned a tz-aware UTC datetime
        assert result.tzinfo is not None
        assert "flex_when_generated_parse_failed" in out


class TestFetchSnapshotIntegration:
    def test_full_path_send_then_poll_then_parse(self):
        transport = _mock_transport(
            [
                httpx.Response(200, content=_fixture("send_request_success.xml")),
                httpx.Response(200, content=_fixture("err_generation_in_progress.xml")),
                httpx.Response(200, content=_fixture("full_paper.xml")),
            ]
        )
        client = FlexClient(
            token="t",
            query_id=123,
            transport=transport,
            poll_interval_seconds=0,
            max_wait_seconds=10,
        )
        snap = client.fetch_snapshot()
        assert snap.account_id == "DU1234567"
        assert client.reference_code == "1234567890"

    def test_flex_client_context_manager_closes(self):
        transport = _mock_transport(
            [
                httpx.Response(200, content=_fixture("send_request_success.xml")),
                httpx.Response(200, content=_fixture("full_paper.xml")),
            ]
        )
        with FlexClient(
            token="t",
            query_id=1,
            transport=transport,
            poll_interval_seconds=0,
            max_wait_seconds=10,
        ) as client:
            snap = client.fetch_snapshot()
            assert snap.account_id == "DU1234567"
        # After exit, underlying httpx.Client is closed
        assert client._client.is_closed

    def test_reference_code_preserved_on_timeout(self):
        in_progress = _fixture("err_generation_in_progress.xml")
        transport = _mock_transport(
            [
                httpx.Response(200, content=_fixture("send_request_success.xml")),
                *[httpx.Response(200, content=in_progress) for _ in range(5)],
            ]
        )
        client = FlexClient(
            token="t",
            query_id=1,
            transport=transport,
            poll_interval_seconds=0,
            max_wait_seconds=0,
        )
        with pytest.raises(FlexReportTimeoutError) as excinfo:
            client.fetch_snapshot()
        assert excinfo.value.reference_code == "1234567890"
        assert client.reference_code == "1234567890"  # also available on instance
