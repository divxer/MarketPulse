"""IBKR Flex Web Service read-only adapter.

Phase 7a-Flex transport. Replaces the gnzsnz/ib-gateway sidecar + ibapi
adapter. Pure HTTPS, no daemon, no Java Gateway, no 2FA, no daily forced
logout. See docs/superpowers/specs/2026-05-24-phase-7a-flex-readonly-sync-design.md.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Final

import httpx

from marketpulse.broker.types import (
    BrokerSnapshot,
    classify_broker_environment_from_account_id,  # noqa: F401 — used by T2 parser
)
from marketpulse.logging import get_logger

log = get_logger(__name__)

DEFAULT_BASE_URL: Final = "https://gdcdyn.interactivebrokers.com/Universal/servlet"


class FlexError(Exception):
    """Base class for Flex Web Service errors. Subclass name appears in
    broker_sync_run.error_type for runbook correlation."""


class FlexHttpError(FlexError):
    """Transport-layer failure: DNS, TLS, refused, timeout, 5xx."""


class FlexAuthError(FlexError):
    """Token rejected or query owned by another user. Returned as XML
    ErrorCode 1003 / 1011 / 1012 by IBKR."""


class FlexSendRequestError(FlexError):
    """SendRequest returned HTTP 200 with an XML error body that is not
    auth-related."""


class FlexReportTimeoutError(FlexError):
    """Poll exhausted ibkr_flex_max_wait_seconds while IBKR kept returning
    'generation in progress'. The reference_code attribute lets the
    operator manually re-fetch later via GetStatement."""

    def __init__(self, message: str, reference_code: str | None) -> None:
        super().__init__(message)
        self.reference_code = reference_code


class FlexStatementError(FlexError):
    """GetStatement returned HTTP 200 with an XML error body after the
    report should have been ready."""


class FlexParseError(FlexError):
    """XML is malformed, or Account section is missing, or accountId is
    missing from Account section. Optional sections (Cash, Positions,
    Trades) being absent does NOT raise this."""


class FlexAccountMismatchError(FlexError):
    """Returned report's accountId disagrees with settings.ibkr_account_id."""


class LiveAccountRefusedError(FlexError):
    """Report classification is not 'paper' but mp_ibkr_allow_live is False.
    'unknown' classifications also trigger this (L21 conservative brake)."""


@dataclass(frozen=True)
class _PollResult:
    ready: bool
    xml: bytes | None
    reference_code: str | None


class FlexClient:
    """IBKR Flex Web Service adapter implementing BrokerReadClient.

    Two-phase fetch: SendRequest gets a ReferenceCode, then poll
    GetStatement until ready (bounded by max_wait_seconds).
    """

    def __init__(
        self,
        *,
        token: str,
        query_id: int,
        account_id: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        poll_interval_seconds: int = 5,
        max_wait_seconds: int = 60,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = token
        self._query_id = query_id
        self._account_id = account_id or None
        self._base_url = base_url.rstrip("/")
        self._poll_interval = poll_interval_seconds
        self._max_wait = max_wait_seconds
        self._reference_code: str | None = None
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(connect=5, read=30, write=10, pool=5),
        )

    # ---- BrokerReadClient ----

    def fetch_snapshot(self) -> BrokerSnapshot:
        xml = self._fetch_xml()
        return self._parse_snapshot(xml)

    @property
    def reference_code(self) -> str | None:
        """Last SendRequest reference code. Useful for forensic re-fetch."""
        return self._reference_code

    # ---- internals ----

    def _send_request(self) -> str:
        url = f"{self._base_url}/FlexStatementService.SendRequest"
        params = {"t": self._token, "q": str(self._query_id), "v": "3"}
        try:
            resp = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise FlexHttpError(f"{type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 500:
            raise FlexHttpError(f"SendRequest returned HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise FlexHttpError(f"SendRequest returned HTTP {resp.status_code}")

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise FlexParseError(f"SendRequest body not XML: {exc}") from exc

        status = (root.findtext("Status") or "").strip()
        error_code = (root.findtext("ErrorCode") or "").strip()
        error_message = (root.findtext("ErrorMessage") or "").strip()
        reference = (root.findtext("ReferenceCode") or "").strip() or None

        if status == "Success" and reference:
            self._reference_code = reference
            return reference
        if error_code in {"1003", "1011", "1012"}:
            raise FlexAuthError(f"{error_code}: {error_message}")
        raise FlexSendRequestError(f"{error_code or 'unknown'}: {error_message or status}")

    def _get_statement(self, reference_code: str) -> _PollResult:
        url = f"{self._base_url}/FlexStatementService.GetStatement"
        params = {"t": self._token, "q": reference_code, "v": "3"}
        try:
            resp = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise FlexHttpError(f"{type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 500:
            raise FlexHttpError(f"GetStatement returned HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise FlexHttpError(f"GetStatement returned HTTP {resp.status_code}")

        body = resp.content
        # If body parses as FlexStatementResponse with Status=Warn/Fail, it's a status reply,
        # not the actual report.
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise FlexParseError(f"GetStatement body not XML: {exc}") from exc
        if root.tag == "FlexStatementResponse":
            status = (root.findtext("Status") or "").strip()
            error_code = (root.findtext("ErrorCode") or "").strip()
            error_message = (root.findtext("ErrorMessage") or "").strip()
            if status == "Warn":
                return _PollResult(ready=False, xml=None, reference_code=reference_code)
            if error_code in {"1003", "1011", "1012"}:
                raise FlexAuthError(f"{error_code}: {error_message}")
            raise FlexStatementError(f"{error_code or 'unknown'}: {error_message or status}")
        return _PollResult(ready=True, xml=body, reference_code=reference_code)

    def _fetch_xml(self) -> bytes:
        reference = self._send_request()
        deadline = time.monotonic() + self._max_wait
        while True:
            poll = self._get_statement(reference)
            if poll.ready and poll.xml is not None:
                return poll.xml
            if time.monotonic() >= deadline:
                raise FlexReportTimeoutError(
                    f"Flex report not ready after {self._max_wait}s",
                    reference_code=reference,
                )
            if self._poll_interval > 0:
                time.sleep(self._poll_interval)

    def _parse_snapshot(self, xml: bytes) -> BrokerSnapshot:  # pragma: no cover
        # Implemented in Task 2.
        raise NotImplementedError
