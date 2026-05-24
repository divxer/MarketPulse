"""Phase 7a-Flex FlexClient tests."""
# Layer: unit

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from marketpulse.broker.flex_client import (
    FlexAuthError,
    FlexClient,
    FlexHttpError,
    FlexReportTimeoutError,
    FlexSendRequestError,
    FlexStatementError,
)

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
        transport = _mock_transport([
            httpx.Response(200, content=_fixture("send_request_success.xml")),
        ])
        client = FlexClient(token="t", query_id=123, transport=transport)
        ref = client._send_request()
        assert ref == "1234567890"

    def test_xml_error_code_raises_send_request_error(self):
        transport = _mock_transport([
            httpx.Response(200, content=_fixture("err_send_request_bad_query.xml")),
        ])
        client = FlexClient(token="t", query_id=999, transport=transport)
        with pytest.raises(FlexSendRequestError, match="1019"):
            client._send_request()

    def test_xml_auth_error_raises_auth_error(self):
        transport = _mock_transport([
            httpx.Response(200, content=_fixture("err_auth.xml")),
        ])
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
        transport = _mock_transport([
            httpx.Response(200, content=_fixture("send_request_success.xml")),
            httpx.Response(200, content=_fixture("full_paper.xml")),
        ])
        client = FlexClient(
            token="t", query_id=1, transport=transport,
            poll_interval_seconds=0, max_wait_seconds=10,
        )
        xml = client._fetch_xml()
        assert b"AccountInformation" in xml

    def test_one_poll_in_progress_then_ready(self):
        transport = _mock_transport([
            httpx.Response(200, content=_fixture("send_request_success.xml")),
            httpx.Response(200, content=_fixture("err_generation_in_progress.xml")),
            httpx.Response(200, content=_fixture("full_paper.xml")),
        ])
        client = FlexClient(
            token="t", query_id=1, transport=transport,
            poll_interval_seconds=0, max_wait_seconds=10,
        )
        xml = client._fetch_xml()
        assert b"AccountInformation" in xml

    def test_timeout_raises_with_reference_code(self):
        in_progress = _fixture("err_generation_in_progress.xml")
        transport = _mock_transport([
            httpx.Response(200, content=_fixture("send_request_success.xml")),
            *[httpx.Response(200, content=in_progress) for _ in range(20)],
        ])
        client = FlexClient(
            token="t", query_id=1, transport=transport,
            poll_interval_seconds=0, max_wait_seconds=0,  # immediate timeout
        )
        with pytest.raises(FlexReportTimeoutError) as excinfo:
            client._fetch_xml()
        assert excinfo.value.reference_code == "1234567890"

    def test_statement_xml_error_after_ready_window(self):
        transport = _mock_transport([
            httpx.Response(200, content=_fixture("send_request_success.xml")),
            httpx.Response(200, content=_fixture("err_report_expired.xml")),
        ])
        client = FlexClient(
            token="t", query_id=1, transport=transport,
            poll_interval_seconds=0, max_wait_seconds=10,
        )
        with pytest.raises(FlexStatementError):
            client._fetch_xml()
