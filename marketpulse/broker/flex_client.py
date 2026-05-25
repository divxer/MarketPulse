"""IBKR Flex Web Service read-only adapter.

Phase 7a-Flex transport. Replaces the gnzsnz/ib-gateway sidecar + ibapi
adapter. Pure HTTPS, no daemon, no Java Gateway, no 2FA, no daily forced
logout. See docs/superpowers/specs/2026-05-24-phase-7a-flex-readonly-sync-design.md.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Final
from zoneinfo import ZoneInfo

import httpx

from marketpulse.broker.types import (
    BrokerAccount,
    BrokerCash,
    BrokerExecution,
    BrokerPosition,
    BrokerSnapshot,
    classify_broker_environment_from_account_id,
)
from marketpulse.logging import get_logger

log = get_logger(__name__)

DEFAULT_BASE_URL: Final = "https://gdcdyn.interactivebrokers.com/Universal/servlet"

# IBKR XML error codes that indicate token/query auth failures. Shared by
# SendRequest and GetStatement.
_AUTH_ERROR_CODES: Final = frozenset({"1003", "1011", "1012"})


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

    # ---- lifecycle ----

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> FlexClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

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
        # Note: IBKR's documented "generation in progress" retry semantics
        # for SendRequest are not handled here — low frequency in practice;
        # callers retry the whole sync run if SendRequest itself flakes.
        url = f"{self._base_url}/FlexStatementService.SendRequest"
        params = {"t": self._token, "q": str(self._query_id), "v": "3"}
        try:
            resp = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise FlexHttpError(f"{type(exc).__name__}: {exc}") from exc
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
        if error_code in _AUTH_ERROR_CODES:
            raise FlexAuthError(f"{error_code}: {error_message}")
        raise FlexSendRequestError(f"{error_code or 'unknown'}: {error_message or status}")

    def _get_statement(self, reference_code: str) -> _PollResult:
        url = f"{self._base_url}/FlexStatementService.GetStatement"
        params = {"t": self._token, "q": reference_code, "v": "3"}
        try:
            resp = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise FlexHttpError(f"{type(exc).__name__}: {exc}") from exc
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
            if error_code in _AUTH_ERROR_CODES:
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

    def _parse_snapshot(self, xml: bytes) -> BrokerSnapshot:
        """Parse a Flex Activity report into a BrokerSnapshot.

        Per L21: AccountInformation is REQUIRED; CashReport, OpenPositions
        and Trades are OPTIONAL (absence yields empty tuples, not errors).
        Per L18: open_orders is always (); Activity Flex does not produce
        open orders.

        When ``account_id`` is set on the client, the matching FlexStatement
        is selected; otherwise the first FlexStatement is used.
        """
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise FlexParseError(f"Flex XML not parseable: {exc}") from exc

        statements = root.findall(".//FlexStatement")
        if not statements:
            raise FlexParseError("FlexStatement element not found")

        statement = self._select_statement(statements)
        account_el = statement.find("AccountInformation")
        if account_el is None:
            raise FlexParseError("AccountInformation section is required (L21)")
        account_id = (account_el.get("accountId") or "").strip()
        if not account_id:
            raise FlexParseError("AccountInformation/accountId must be non-empty")

        captured_at = self._parse_when_generated(statement.get("whenGenerated"))
        environment = classify_broker_environment_from_account_id(account_id)

        return BrokerSnapshot(
            broker="IBKR",
            broker_environment=environment,
            account_id=account_id,
            captured_at=captured_at,
            account=self._parse_account(statement, account_el, account_id),
            cash=self._parse_cash(statement, account_id),
            positions=self._parse_positions(statement, account_id),
            open_orders=(),  # L18: Flex Activity never produces open orders
            executions=self._parse_executions(statement, account_id),
        )

    def _select_statement(self, statements: list[ET.Element]) -> ET.Element:
        """Filter to the configured account_id when set; otherwise return first.

        When no account_id filter is set and multiple statements are present,
        we log a WARNING and return the first one — preserves prior behavior,
        but surfaces the ambiguity in logs for operators.
        """
        if self._account_id:
            for st in statements:
                if st.get("accountId") == self._account_id:
                    return st
            raise FlexAccountMismatchError(
                f"Configured account {self._account_id} not in report; "
                f"available: {[s.get('accountId') for s in statements]}"
            )
        if len(statements) > 1:
            log.warning(
                "flex_multi_account_no_filter",
                available=[s.get("accountId") for s in statements],
                selected=statements[0].get("accountId"),
            )
        return statements[0]

    @staticmethod
    def _parse_when_generated(value: str | None) -> datetime:
        """Parse 'YYYYMMDD;HHMMSS' format (NY local) into UTC datetime.

        Falls back to current UTC if value missing or unparseable; emits a
        WARNING log on fallback so operators see drift in captured_at.
        """
        if not value:
            log.warning("flex_when_generated_parse_failed", value=value)
            return datetime.now(UTC)
        try:
            date_part, time_part = value.split(";")
            local = datetime.strptime(f"{date_part}{time_part}", "%Y%m%d%H%M%S")
            return local.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)
        except (ValueError, IndexError):
            log.warning("flex_when_generated_parse_failed", value=value)
            return datetime.now(UTC)

    @staticmethod
    def _decimal(value: str | None) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            return Decimal(value)
        except InvalidOperation:
            return None

    def _parse_account(
        self, statement: ET.Element, el: ET.Element, account_id: str
    ) -> BrokerAccount:
        # NLV is NOT in AccountInformation (which only carries metadata like
        # accountType / baseCurrency / contact details). It comes from
        # EquitySummaryInBase.total. Likewise buyingPower / maintenanceMargin
        # / excessLiquidity are live-only TWS concepts — Activity Flex never
        # exports them, so we leave them None.
        return BrokerAccount(
            account_id=account_id,
            account_type=el.get("accountType"),
            base_currency=el.get("baseCurrency"),
            net_liquidation=self._extract_net_liquidation(statement),
            buying_power=None,
            maintenance_margin=None,
            excess_liquidity=None,
        )

    def _extract_net_liquidation(self, statement: ET.Element) -> Decimal | None:
        """Pull NLV from EquitySummaryInBase / EquitySummaryByReportDateInBase.

        Both element names appear in IBKR Activity Flex output depending on
        report period; both carry a ``total`` attribute. When multiple rows
        are present (multi-day reports), pick the latest by ``reportDate``
        (yyyymmdd string — lexicographic sort is correct).
        """
        rows: list[ET.Element] = []
        for tag in ("EquitySummaryInBase", "EquitySummaryByReportDateInBase"):
            rows.extend(statement.findall(f".//{tag}"))
        if not rows:
            return None
        rows.sort(key=lambda r: r.get("reportDate") or "", reverse=True)
        return self._decimal(rows[0].get("total"))

    def _parse_cash(self, statement: ET.Element, account_id: str) -> tuple[BrokerCash, ...]:
        rows: list[BrokerCash] = []
        for el in statement.findall(".//CashReportCurrency"):
            rows.append(
                BrokerCash(
                    account_id=account_id,
                    currency=el.get("currency") or "",
                    cash_balance=self._decimal(el.get("endingCash")),
                    settled_cash=self._decimal(el.get("endingSettledCash")),
                    accrued_interest=self._decimal(el.get("accruedInterest")),
                )
            )
        return tuple(rows)

    def _parse_positions(
        self,
        statement: ET.Element,
        account_id: str,
    ) -> tuple[BrokerPosition, ...]:
        rows: list[BrokerPosition] = []
        for el in statement.findall(".//OpenPosition"):
            qty = self._decimal(el.get("position")) or Decimal(0)
            rows.append(
                BrokerPosition(
                    account_id=account_id,
                    symbol=el.get("symbol") or "",
                    asset_class=el.get("assetCategory"),
                    quantity=qty,
                    avg_cost=self._decimal(el.get("costBasisPrice")),
                    market_price=self._decimal(el.get("markPrice")),
                    market_value=self._decimal(el.get("positionValue")),
                    unrealized_pnl=self._decimal(el.get("fifoPnlUnrealized")),
                    realized_pnl=self._decimal(el.get("realizedPnl")),
                )
            )
        return tuple(rows)

    def _parse_executions(
        self,
        statement: ET.Element,
        account_id: str,
    ) -> tuple[BrokerExecution, ...]:
        rows: list[BrokerExecution] = []
        for el in statement.findall(".//Trade"):
            rows.append(
                BrokerExecution(
                    account_id=account_id,
                    broker_exec_id=el.get("tradeID") or "",
                    broker_order_id=el.get("ibOrderID"),
                    symbol=el.get("symbol"),
                    side=(el.get("buySell") or "").upper() or None,
                    quantity=self._decimal(el.get("quantity")),
                    price=self._decimal(el.get("tradePrice")),
                    executed_at=self._parse_when_generated(el.get("dateTime")),
                )
            )
        return tuple(rows)
