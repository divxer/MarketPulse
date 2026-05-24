# Phase 7a-Flex — IBKR Read-Only Broker Sync via Flex Web Service — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the IB Gateway sidecar transport (ibapi over TCP) with IBKR's official Flex Web Service (HTTPS+XML), keeping the Phase 7a `broker_*` schema, repository, sync_run state machine, and CLI name unchanged. After this plan: no Gateway container, no `ibapi` dep, one new `FlexClient` adapter, refactored `SyncResult`.

**Architecture:** Swap one adapter behind the existing `BrokerReadClient` Protocol. New file `marketpulse/broker/flex_client.py` (HTTP + XML parser + adapter). Refactor `SyncResult` to Flex-shaped fields. Live-account brake moves from port-based (Gateway) to account-id-based (Flex). DB unchanged.

**Tech Stack:** `httpx` (already a dependency), Python stdlib `xml.etree.ElementTree`, pydantic-settings (`marketpulse.config.Settings`), pytest fixtures from XML files under `tests/broker/fixtures/flex/`.

**Spec:** `docs/superpowers/specs/2026-05-24-phase-7a-flex-readonly-sync-design.md` — 18 lock points (L1-L23). Plan tasks below honor every lock.

**Branch:** `plan/phase-7a-flex-readonly-sync` (already created off clean `main`; spec commit `1d4d001` is the only commit beyond `main`).

---

## File Map

| Path | Action |
|---|---|
| `marketpulse/broker/flex_client.py` | **create** |
| `marketpulse/broker/types.py` | modify (refactor SyncResult, add classifier + errors) |
| `marketpulse/broker/readonly_sync.py` | modify (FlexSyncConfig, brakes, reference_code) |
| `marketpulse/broker/ibkr_client.py` | **delete** |
| `marketpulse/broker/__init__.py` | modify (re-exports) |
| `marketpulse/config.py` | modify (Flex settings, drop Gateway settings) |
| `scripts/sync_ibkr_readonly.py` | modify (DI + flags + output) |
| `pyproject.toml` | modify (drop `ibapi` dep) |
| `uv.lock` | regenerate |
| `.env.example` | modify (Phase 7a section rewrite) |
| `docker-compose.cn.yml` | modify (L23 sidecar cleanup) |
| `docker-compose.prod.yml` | modify (L23 sidecar cleanup) |
| `DEPLOY.md` | modify if it mentions gateway/VNC/TWS |
| `docs/operations/ibkr-readonly-sync-runbook.md` | full rewrite |
| `docs/superpowers/specs/2026-05-23-phase-7a-ibkr-readonly-sync-design.md` | add header pointer |
| `tests/broker/fixtures/flex/*.xml` | **create** (11 fixture files) |
| `tests/broker/test_flex_client.py` | **create** |
| `tests/broker/test_ibkr_client_mapping.py` | **delete** |
| `tests/broker/test_readonly_sync.py` | modify (FlexSyncConfig) |
| `tests/broker/test_sync_cli.py` | modify (new flags + output) |
| `tests/broker/test_types_and_contract.py` | modify (SyncResult + classifier) |
| `tests/architecture/test_phase7a_ibkr_readonly_boundary.py` | modify (allow-list → deny-list) |

---

## Task Sequencing Rationale

T1-T3 build `flex_client.py` in isolation (new file, no existing-code coupling). T4 adds new settings without removing old (so existing tests still resolve). T5 is the atomic "rip Gateway, swap types" task — biggest single change but unavoidable because `types.py` `SyncResult` shape is shared. T6 is the new sync orchestration; T7 the CLI. T8 drops the now-unused Gateway settings. T9-T11 are docs and compose. T12 is the integration sweep + branch push.

---

### Task 1: `FlexClient` HTTP transport layer

**Files:**
- Create: `marketpulse/broker/flex_client.py`
- Create: `tests/broker/test_flex_client.py`

This task adds the HTTP scaffolding (SendRequest + GetStatement + polling loop) plus error classes. No XML parsing yet — parsing comes in T2.

- [ ] **Step 1: Create error classes**

Create `marketpulse/broker/flex_client.py` with the FlexError hierarchy:

```python
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

import httpx

from marketpulse.broker.types import (
    BrokerAccount,
    BrokerCash,
    BrokerEnvironment,
    BrokerExecution,
    BrokerOpenOrder,
    BrokerPosition,
    BrokerSnapshot,
    classify_broker_environment_from_account_id,
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
```

- [ ] **Step 2: Write failing tests for FlexClient HTTP layer**

Create `tests/broker/test_flex_client.py`:

```python
"""Phase 7a-Flex FlexClient tests."""
# Layer: unit

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from marketpulse.broker.flex_client import (
    DEFAULT_BASE_URL,
    FlexAuthError,
    FlexClient,
    FlexHttpError,
    FlexParseError,
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
```

- [ ] **Step 3: Create XML fixtures (subset needed for this task)**

Create these files under `tests/broker/fixtures/flex/`:

`send_request_success.xml`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<FlexStatementResponse timestamp="20 December, 2024 11:30 AM EST">
  <Status>Success</Status>
  <ReferenceCode>1234567890</ReferenceCode>
  <Url>https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement</Url>
</FlexStatementResponse>
```

`err_auth.xml`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<FlexStatementResponse timestamp="20 December, 2024 11:30 AM EST">
  <Status>Fail</Status>
  <ErrorCode>1012</ErrorCode>
  <ErrorMessage>Token has expired or is invalid.</ErrorMessage>
</FlexStatementResponse>
```

`err_send_request_bad_query.xml`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<FlexStatementResponse timestamp="20 December, 2024 11:30 AM EST">
  <Status>Fail</Status>
  <ErrorCode>1019</ErrorCode>
  <ErrorMessage>Query ID is invalid.</ErrorMessage>
</FlexStatementResponse>
```

`err_generation_in_progress.xml`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<FlexStatementResponse timestamp="20 December, 2024 11:30 AM EST">
  <Status>Warn</Status>
  <ErrorCode>1019</ErrorCode>
  <ErrorMessage>Statement generation in progress. Please try again shortly.</ErrorMessage>
  <ReferenceCode>1234567890</ReferenceCode>
</FlexStatementResponse>
```

`err_report_expired.xml`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<FlexStatementResponse timestamp="20 December, 2024 11:30 AM EST">
  <Status>Fail</Status>
  <ErrorCode>1009</ErrorCode>
  <ErrorMessage>Reference code expired.</ErrorMessage>
</FlexStatementResponse>
```

`full_paper.xml` (minimal valid Activity Flex report — DU account, 3 positions, 2 cash, 2 trades):
```xml
<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="MarketPulse_7a" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="DU1234567" fromDate="20241219" toDate="20241220" period="LastBusinessDay" whenGenerated="20241220;113000">
      <AccountInformation accountId="DU1234567" accountType="DEMO" customerType="INDIVIDUAL" baseCurrency="USD" netLiquidationValue="100000.00" buyingPower="400000.00" maintenanceMarginReq="0.00" excessLiquidity="100000.00"/>
      <CashReport>
        <CashReportCurrency accountId="DU1234567" currency="USD" endingCash="50000.00" endingSettledCash="50000.00" accruedInterest="0.00"/>
        <CashReportCurrency accountId="DU1234567" currency="HKD" endingCash="0.00" endingSettledCash="0.00" accruedInterest="0.00"/>
      </CashReport>
      <OpenPositions>
        <OpenPosition accountId="DU1234567" symbol="AAPL" assetCategory="STK" position="100" costBasisPrice="170.50" markPrice="175.20" positionValue="17520.00" fifoPnlUnrealized="470.00" realizedPnl="0.00"/>
        <OpenPosition accountId="DU1234567" symbol="MSFT" assetCategory="STK" position="50" costBasisPrice="380.00" markPrice="395.10" positionValue="19755.00" fifoPnlUnrealized="755.00" realizedPnl="0.00"/>
        <OpenPosition accountId="DU1234567" symbol="0700" assetCategory="STK" position="200" costBasisPrice="380.50" markPrice="395.00" positionValue="79000.00" fifoPnlUnrealized="2900.00" realizedPnl="0.00"/>
      </OpenPositions>
      <Trades>
        <Trade accountId="DU1234567" tradeID="9876543210" ibOrderID="ORD001" symbol="AAPL" assetCategory="STK" buySell="BUY" quantity="100" tradePrice="170.50" dateTime="20241219;143015"/>
        <Trade accountId="DU1234567" tradeID="9876543211" ibOrderID="ORD002" symbol="MSFT" assetCategory="STK" buySell="BUY" quantity="50" tradePrice="380.00" dateTime="20241219;143108"/>
      </Trades>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
```

- [ ] **Step 4: Implement `FlexClient` HTTP scaffolding**

Append to `marketpulse/broker/flex_client.py`:

```python
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
```

- [ ] **Step 5: Run tests, verify HTTP-layer tests pass**

Run: `uv run pytest tests/broker/test_flex_client.py -v -k "TestSendRequest or TestGetStatementPolling"`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/broker/flex_client.py tests/broker/test_flex_client.py tests/broker/fixtures/flex/
git commit -m "feat(7a-flex): FlexClient HTTP transport layer + error taxonomy"
```

---

### Task 2: `FlexClient` XML parser + fetch_snapshot

**Files:**
- Modify: `marketpulse/broker/flex_client.py`
- Modify: `tests/broker/test_flex_client.py`
- Create more fixtures under `tests/broker/fixtures/flex/`

This task implements XML→DTO mapping per L21 (Account required, others optional).

- [ ] **Step 1: Add classifier helper (forward import)**

Edit `marketpulse/broker/types.py`. Add a stub for `classify_broker_environment_from_account_id` that flex_client.py imports — this will be properly implemented in Task 5. For now:

```python
def classify_broker_environment_from_account_id(account_id: str) -> BrokerEnvironment:
    """Phase 7a-Flex classifier. See L21 in design spec.
    Full implementation in T5 — this stub is so T2 tests can import."""
    import re
    if re.fullmatch(r"DU\d+", account_id):
        return "paper"
    if re.fullmatch(r"U\d+", account_id):
        return "live"
    return "unknown"
```

- [ ] **Step 2: Create remaining fixtures**

Under `tests/broker/fixtures/flex/`:

- `full_live.xml` — same structure as `full_paper.xml` but `accountId="U1234567"` and `accountType="INDIVIDUAL"` throughout.
- `account_only.xml` — Account section only, no `<CashReport>` / `<OpenPositions>` / `<Trades>`.
- `missing_account.xml` — `<FlexStatement>` with no `<AccountInformation>` child.
- `missing_account_id.xml` — `<AccountInformation accountId=""/>` (empty string).
- `multi_currency.xml` — DU account with 4 currencies in CashReport (USD, HKD, JPY, CAD).
- `multi_account.xml` — two `<FlexStatement>` with different accountIds (DU... and DU...).
- `malformed.xml` — literal text `<not xml`.

(Operator note: full XML samples should follow the same structure as `full_paper.xml` from Task 1.)

- [ ] **Step 3: Write failing parser tests**

Append to `tests/broker/test_flex_client.py`:

```python
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
```

- [ ] **Step 4: Implement parser**

Replace the `_parse_snapshot` stub with a real implementation:

```python
def _parse_snapshot(self, xml: bytes) -> BrokerSnapshot:
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
        account=self._parse_account(account_el, account_id),
        cash=self._parse_cash(statement, account_id),
        positions=self._parse_positions(statement, account_id),
        open_orders=(),  # L18: Flex Activity never produces open orders
        executions=self._parse_executions(statement, account_id),
    )

def _select_statement(self, statements: list[ET.Element]) -> ET.Element:
    """Filter to the configured account_id when set; otherwise return first."""
    if self._account_id:
        for st in statements:
            if st.get("accountId") == self._account_id:
                return st
        raise FlexAccountMismatchError(
            f"Configured account {self._account_id} not in report; "
            f"available: {[s.get('accountId') for s in statements]}"
        )
    return statements[0]

@staticmethod
def _parse_when_generated(value: str | None) -> datetime:
    """Parse 'YYYYMMDD;HHMMSS' format (NY local) into UTC datetime.

    Falls back to current UTC if value missing or unparseable.
    """
    if not value:
        return datetime.now(UTC)
    try:
        from zoneinfo import ZoneInfo
        date_part, time_part = value.split(";")
        local = datetime.strptime(f"{date_part}{time_part}", "%Y%m%d%H%M%S")
        return local.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)
    except (ValueError, IndexError):
        return datetime.now(UTC)

@staticmethod
def _decimal(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None

def _parse_account(self, el: ET.Element, account_id: str) -> BrokerAccount:
    return BrokerAccount(
        account_id=account_id,
        account_type=el.get("accountType"),
        base_currency=el.get("baseCurrency"),
        net_liquidation=self._decimal(el.get("netLiquidationValue")),
        buying_power=self._decimal(el.get("buyingPower")),
        maintenance_margin=self._decimal(el.get("maintenanceMarginReq")),
        excess_liquidity=self._decimal(el.get("excessLiquidity")),
    )

def _parse_cash(self, statement: ET.Element, account_id: str) -> tuple[BrokerCash, ...]:
    rows: list[BrokerCash] = []
    for el in statement.findall(".//CashReportCurrency"):
        rows.append(BrokerCash(
            account_id=account_id,
            currency=el.get("currency") or "",
            cash_balance=self._decimal(el.get("endingCash")),
            settled_cash=self._decimal(el.get("endingSettledCash")),
            accrued_interest=self._decimal(el.get("accruedInterest")),
        ))
    return tuple(rows)

def _parse_positions(self, statement: ET.Element, account_id: str) -> tuple[BrokerPosition, ...]:
    rows: list[BrokerPosition] = []
    for el in statement.findall(".//OpenPosition"):
        qty = self._decimal(el.get("position")) or Decimal(0)
        rows.append(BrokerPosition(
            account_id=account_id,
            symbol=el.get("symbol") or "",
            asset_class=el.get("assetCategory"),
            quantity=qty,
            avg_cost=self._decimal(el.get("costBasisPrice")),
            market_price=self._decimal(el.get("markPrice")),
            market_value=self._decimal(el.get("positionValue")),
            unrealized_pnl=self._decimal(el.get("fifoPnlUnrealized")),
            realized_pnl=self._decimal(el.get("realizedPnl")),
        ))
    return tuple(rows)

def _parse_executions(self, statement: ET.Element, account_id: str) -> tuple[BrokerExecution, ...]:
    rows: list[BrokerExecution] = []
    for el in statement.findall(".//Trade"):
        rows.append(BrokerExecution(
            account_id=account_id,
            broker_exec_id=el.get("tradeID") or "",
            broker_order_id=el.get("ibOrderID"),
            symbol=el.get("symbol"),
            side=(el.get("buySell") or "").upper() or None,
            quantity=self._decimal(el.get("quantity")),
            price=self._decimal(el.get("tradePrice")),
            executed_at=self._parse_when_generated(el.get("dateTime")),
        ))
    return tuple(rows)
```

- [ ] **Step 5: Run parser tests, verify pass**

Run: `uv run pytest tests/broker/test_flex_client.py::TestParser -v`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/broker/flex_client.py marketpulse/broker/types.py tests/broker/test_flex_client.py tests/broker/fixtures/flex/
git commit -m "feat(7a-flex): FlexClient XML parser with required-Account / optional-rest semantics"
```

---

### Task 3: End-to-end `fetch_snapshot` integration

**Files:**
- Modify: `tests/broker/test_flex_client.py`

- [ ] **Step 1: Write integration test**

Append to `tests/broker/test_flex_client.py`:

```python
class TestFetchSnapshotIntegration:
    def test_full_path_send_then_poll_then_parse(self):
        transport = _mock_transport([
            httpx.Response(200, content=_fixture("send_request_success.xml")),
            httpx.Response(200, content=_fixture("err_generation_in_progress.xml")),
            httpx.Response(200, content=_fixture("full_paper.xml")),
        ])
        client = FlexClient(
            token="t", query_id=123, transport=transport,
            poll_interval_seconds=0, max_wait_seconds=10,
        )
        snap = client.fetch_snapshot()
        assert snap.account_id == "DU1234567"
        assert client.reference_code == "1234567890"

    def test_reference_code_preserved_on_timeout(self):
        in_progress = _fixture("err_generation_in_progress.xml")
        transport = _mock_transport([
            httpx.Response(200, content=_fixture("send_request_success.xml")),
            *[httpx.Response(200, content=in_progress) for _ in range(5)],
        ])
        client = FlexClient(
            token="t", query_id=1, transport=transport,
            poll_interval_seconds=0, max_wait_seconds=0,
        )
        with pytest.raises(FlexReportTimeoutError) as excinfo:
            client.fetch_snapshot()
        assert excinfo.value.reference_code == "1234567890"
        assert client.reference_code == "1234567890"  # also available on instance
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/broker/test_flex_client.py -v
git add tests/broker/test_flex_client.py
git commit -m "test(7a-flex): FlexClient end-to-end fetch_snapshot integration"
```

---

### Task 4: Add Flex settings (additions only)

**Files:**
- Modify: `marketpulse/config.py`
- Modify: existing settings test if there is one

- [ ] **Step 1: Add Flex fields to Settings**

In `marketpulse/config.py`, append after the existing Gateway fields (do NOT remove the Gateway fields yet — that's Task 7):

```python
    # Phase 7a-Flex IBKR read-only sync via Flex Web Service.
    ibkr_flex_token: str = Field("", alias="IBKR_FLEX_TOKEN")
    ibkr_flex_query_id: int = Field(0, alias="IBKR_FLEX_QUERY_ID", ge=0)
    ibkr_flex_base_url: str = Field(
        "https://gdcdyn.interactivebrokers.com/Universal/servlet",
        alias="IBKR_FLEX_BASE_URL",
    )
    ibkr_flex_poll_interval_seconds: int = Field(
        5, alias="IBKR_FLEX_POLL_INTERVAL_SECONDS", ge=0,
    )
    ibkr_flex_max_wait_seconds: int = Field(
        60, alias="IBKR_FLEX_MAX_WAIT_SECONDS", ge=0,
    )
```

- [ ] **Step 2: Add a tiny smoke test for the new settings (or extend existing)**

Append to whichever test file covers `get_settings()` (search for `get_settings` in tests):

```python
def test_flex_settings_have_sane_defaults(monkeypatch):
    monkeypatch.delenv("IBKR_FLEX_TOKEN", raising=False)
    monkeypatch.delenv("IBKR_FLEX_QUERY_ID", raising=False)
    from marketpulse.config import Settings
    s = Settings()
    assert s.ibkr_flex_token == ""
    assert s.ibkr_flex_query_id == 0
    assert s.ibkr_flex_poll_interval_seconds == 5
    assert s.ibkr_flex_max_wait_seconds == 60
    assert "interactivebrokers.com" in s.ibkr_flex_base_url
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/ -k "settings" -v
git add marketpulse/config.py tests/
git commit -m "feat(7a-flex): Flex settings added alongside Gateway settings"
```

---

### Task 5: Atomic transport swap — types, ibapi removal, architecture guard

**Files:**
- Modify: `marketpulse/broker/types.py`
- Delete: `marketpulse/broker/ibkr_client.py`
- Modify: `marketpulse/broker/__init__.py` (drop ibkr_client re-export if any)
- Modify: `pyproject.toml` (drop `ibapi` dep)
- Regenerate: `uv.lock`
- Delete: `tests/broker/test_ibkr_client_mapping.py`
- Modify: `tests/architecture/test_phase7a_ibkr_readonly_boundary.py`
- Modify: `tests/broker/test_types_and_contract.py`

This is the biggest single task — does the type refactor and rips out the ibapi code path atomically.

- [ ] **Step 1: Refactor `marketpulse/broker/types.py`**

Replace the file content with:

```python
"""Pure broker truth DTOs for read-only sync capture (Phase 7a-Flex)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

BrokerName = Literal["IBKR"]
BrokerEnvironment = Literal["paper", "live", "unknown"]
SyncStatus = Literal["started", "completed", "failed"]
Transport = Literal["flex"]


_PAPER_RE = re.compile(r"^DU\d+$")
_LIVE_RE = re.compile(r"^U\d+$")


def classify_broker_environment_from_account_id(account_id: str | None) -> BrokerEnvironment:
    """Classify environment from IBKR account ID prefix (L21).

    DU<digits>     → paper
    U<digits>      → live
    anything else  → unknown   (treated like live by the brake; never falls through)
    """
    if not account_id:
        return "unknown"
    if _PAPER_RE.match(account_id):
        return "paper"
    if _LIVE_RE.match(account_id):
        return "live"
    return "unknown"


@dataclass(frozen=True)
class BrokerAccount:
    account_id: str
    account_type: str | None
    base_currency: str | None
    net_liquidation: Decimal | None
    buying_power: Decimal | None
    maintenance_margin: Decimal | None
    excess_liquidity: Decimal | None


@dataclass(frozen=True)
class BrokerCash:
    account_id: str
    currency: str
    cash_balance: Decimal | None
    settled_cash: Decimal | None
    accrued_interest: Decimal | None


@dataclass(frozen=True)
class BrokerPosition:
    account_id: str
    symbol: str
    asset_class: str | None
    quantity: Decimal
    avg_cost: Decimal | None
    market_price: Decimal | None
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None


@dataclass(frozen=True)
class BrokerOpenOrder:
    account_id: str
    broker_order_id: str
    symbol: str | None
    side: str | None
    order_type: str | None
    quantity: Decimal | None
    limit_price: Decimal | None
    status: str | None


@dataclass(frozen=True)
class BrokerExecution:
    account_id: str
    broker_exec_id: str
    broker_order_id: str | None
    symbol: str | None
    side: str | None
    quantity: Decimal | None
    price: Decimal | None
    executed_at: datetime | None


@dataclass(frozen=True)
class BrokerSnapshot:
    broker: BrokerName
    broker_environment: BrokerEnvironment
    account_id: str
    captured_at: datetime
    account: BrokerAccount
    cash: tuple[BrokerCash, ...]
    positions: tuple[BrokerPosition, ...]
    open_orders: tuple[BrokerOpenOrder, ...]   # always () under Flex transport (L18)
    executions: tuple[BrokerExecution, ...]


@dataclass(frozen=True)
class SyncResult:
    """Phase 7a-Flex result. transport-discriminated shape (L20)."""

    sync_run_id: int
    broker: BrokerName
    broker_environment: BrokerEnvironment
    account_id: str | None
    status: SyncStatus
    transport: Transport
    endpoint: str
    query_id: int | None
    reference_code: str | None = None
    account_snapshots: int = 0
    cash_rows: int = 0
    positions: int = 0
    open_orders: int = 0
    executions: int = 0
    error_type: str | None = None
    error_message: str | None = None
```

- [ ] **Step 2: Delete ibapi adapter and its tests**

```bash
git rm marketpulse/broker/ibkr_client.py tests/broker/test_ibkr_client_mapping.py
```

- [ ] **Step 3: Update `marketpulse/broker/__init__.py`**

Replace any `from .ibkr_client import IbkrReadClient` line with `from .flex_client import FlexClient`. If there was no such re-export, ensure the file no longer references `ibkr_client`. Add `FlexClient` to `__all__` if `__all__` is defined.

- [ ] **Step 4: Drop `ibapi` from `pyproject.toml`**

Remove the `ibapi` line from `[project.dependencies]` (the actual format depends on whether you use `dependencies = [...]` or `[project.optional-dependencies]`). Then:

```bash
uv lock
```

- [ ] **Step 5: Update architecture guard test**

Rewrite `tests/architecture/test_phase7a_ibkr_readonly_boundary.py` to:

```python
"""Phase 7a-Flex boundary: no production module imports ibapi.

The original Phase 7a (gnzsnz/ib-gateway + ibapi) had an ALLOW-LIST: only
``marketpulse/broker/ibkr_client.py`` was permitted to import ``ibapi``.
Phase 7a-Flex removed that adapter entirely; the boundary is now a
DENY-LIST: ``ibapi`` must not appear in any production import.

We keep the file name to preserve git history; the docstring documents
the boundary evolution.
"""
# Layer: architecture

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "marketpulse"


def _python_files() -> list[Path]:
    return [p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_production_module_imports_ibapi():
    offenders: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "ibapi" or alias.name.startswith("ibapi."):
                        offenders.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "ibapi" or (node.module or "").startswith("ibapi."):
                    offenders.append(f"{path}: from {node.module} import ...")
    assert not offenders, "ibapi must not be imported in production code:\n" + "\n".join(offenders)
```

- [ ] **Step 6: Update `test_types_and_contract.py`**

Open the file and find the `classify_broker_environment` references (port-based). Replace with `classify_broker_environment_from_account_id` and update assertions:

```python
import pytest
from marketpulse.broker.types import classify_broker_environment_from_account_id, SyncResult

class TestClassifier:
    @pytest.mark.parametrize("aid,expected", [
        ("DU1234567", "paper"),
        ("DU99999999", "paper"),
        ("U1234567", "live"),
        ("U1", "live"),
        ("", "unknown"),
        (None, "unknown"),
        ("FOO123", "unknown"),
        ("DUabc", "unknown"),
        ("DU", "unknown"),
        ("UA1234", "unknown"),
    ])
    def test_classifier(self, aid, expected):
        assert classify_broker_environment_from_account_id(aid) == expected


def test_sync_result_has_transport_shape():
    sr = SyncResult(
        sync_run_id=1, broker="IBKR", broker_environment="paper",
        account_id="DU1", status="completed",
        transport="flex", endpoint="https://gdcdyn.interactivebrokers.com/Universal/servlet",
        query_id=123, reference_code="ref",
    )
    assert sr.transport == "flex"
    assert sr.query_id == 123
    assert not hasattr(sr, "host")  # L20: removed
    assert not hasattr(sr, "port")
    assert not hasattr(sr, "client_id")
```

- [ ] **Step 7: Update test_readonly_sync.py preview**

There will likely be import errors in `tests/broker/test_readonly_sync.py` after this task (it imports `IbkrSyncConfig` and `classify_broker_environment`). For this task, mark those tests `@pytest.mark.skip(reason="rewritten in T6")` at the top — they'll be fully rewritten in T6 so we don't write throwaway logic.

- [ ] **Step 8: Run tests, expect new failures only in T6 territory**

Run: `uv run pytest tests/broker/ tests/architecture/test_phase7a_ibkr_readonly_boundary.py -v`
Expected: `test_types_and_contract.py` green, `test_flex_client.py` green, architecture guard green, `test_readonly_sync.py` skipped, `test_sync_cli.py` may fail (caught in T7).

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor(7a-flex): rip ibapi adapter, refactor SyncResult to Flex shape, flip architecture guard to deny-list"
```

---

### Task 6: New sync orchestration with Flex brakes

**Files:**
- Modify: `marketpulse/broker/readonly_sync.py`
- Modify: `tests/broker/test_readonly_sync.py` (un-skip + rewrite)

- [ ] **Step 1: Rewrite `marketpulse/broker/readonly_sync.py`**

Full replacement:

```python
"""One-shot read-only broker sync orchestration (Phase 7a-Flex)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from marketpulse.broker.flex_client import (
    FlexClient,
    FlexError,
    FlexReportTimeoutError,
    LiveAccountRefusedError,
)
from marketpulse.broker.read_client import BrokerReadClient
from marketpulse.broker.repository import (
    create_started_run,
    mark_run_completed,
    mark_run_failed,
    persist_snapshot_rows,
)
from marketpulse.broker.types import (
    BrokerEnvironment,
    SyncResult,
    classify_broker_environment_from_account_id,
)
from marketpulse.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class FlexSyncConfig:
    token: str
    query_id: int
    base_url: str
    account_id: str | None
    poll_interval_seconds: int
    max_wait_seconds: int
    allow_live: bool


class AccountMismatchError(RuntimeError):
    """Snapshot's account_id disagrees with configured account_id."""


def _base_context(config: FlexSyncConfig, *, selected_account_id: str | None) -> dict:
    return {
        "transport": "flex",
        "endpoint": config.base_url,
        "query_id": config.query_id,
        "configured_account_id": config.account_id,
        "selected_account_id": selected_account_id,
        "allow_live": config.allow_live,
        "poll_interval_seconds": config.poll_interval_seconds,
        "max_wait_seconds": config.max_wait_seconds,
    }


def run_readonly_sync(
    session: Session,
    *,
    client: BrokerReadClient,
    config: FlexSyncConfig,
    now: datetime | None = None,
) -> SyncResult:
    started_at = (now or datetime.now(UTC)).astimezone(UTC)
    initial_environment: BrokerEnvironment = (
        classify_broker_environment_from_account_id(config.account_id)
        if config.account_id else "unknown"
    )
    run = create_started_run(
        session,
        started_at=started_at,
        broker="IBKR",
        broker_environment=initial_environment,
        account_id=config.account_id,
        context=_base_context(config, selected_account_id=None),
    )

    reference_code: str | None = None
    try:
        snapshot = client.fetch_snapshot()
        reference_code = getattr(client, "reference_code", None)

        if config.account_id and snapshot.account_id != config.account_id:
            raise AccountMismatchError(
                f"Configured account {config.account_id} != returned {snapshot.account_id}"
            )

        # Live-account brake (L21): unknown is treated like live.
        if snapshot.broker_environment != "paper" and not config.allow_live:
            raise LiveAccountRefusedError(
                f"Refusing to capture {snapshot.broker_environment} account "
                f"{snapshot.account_id}; set MP_IBKR_ALLOW_LIVE=true to override"
            )

        counts = persist_snapshot_rows(session, sync_run_id=run.id, snapshot=snapshot)
        context_patch = _base_context(config, selected_account_id=snapshot.account_id)
        if reference_code:
            context_patch["reference_code"] = reference_code
        mark_run_completed(
            session,
            sync_run_id=run.id,
            completed_at=snapshot.captured_at,
            account_id=snapshot.account_id,
            context_patch=context_patch,
        )
        session.flush()
        return SyncResult(
            sync_run_id=run.id,
            broker="IBKR",
            broker_environment=snapshot.broker_environment,
            account_id=snapshot.account_id,
            status="completed",
            transport="flex",
            endpoint=config.base_url,
            query_id=config.query_id,
            reference_code=reference_code,
            **counts,
        )
    except Exception as exc:
        # Preserve reference_code even on failure (L11/L22).
        if reference_code is None:
            reference_code = getattr(client, "reference_code", None)
        if isinstance(exc, FlexReportTimeoutError) and exc.reference_code:
            reference_code = exc.reference_code

        context_patch = _base_context(config, selected_account_id=None)
        if reference_code:
            context_patch["reference_code"] = reference_code

        try:
            mark_run_failed(
                session,
                sync_run_id=run.id,
                completed_at=datetime.now(UTC),
                error_type=type(exc).__name__,
                error_message=str(exc),
                context_patch=context_patch,
            )
            session.flush()
        except Exception as commit_exc:  # noqa: BLE001
            log.warning(
                "broker_sync_mark_run_failed_failed",
                original_error_type=type(exc).__name__,
                original_error=str(exc),
                commit_error_type=type(commit_exc).__name__,
                commit_error=str(commit_exc),
            )
        return SyncResult(
            sync_run_id=run.id,
            broker="IBKR",
            broker_environment=initial_environment,
            account_id=None,
            status="failed",
            transport="flex",
            endpoint=config.base_url,
            query_id=config.query_id,
            reference_code=reference_code,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
```

- [ ] **Step 2: Rewrite `tests/broker/test_readonly_sync.py`**

This test file in the existing repo asserts the sync state machine. Open it, remove the skip marker, and update all `IbkrSyncConfig(...)` constructions to `FlexSyncConfig(...)`. Update assertions touching `host/port/client_id` to use `transport/endpoint/query_id` instead. Add new tests:

```python
class TestFlexBrakes:
    def test_live_account_refused(self, session_factory, make_snapshot):
        # snapshot.broker_environment = "live", allow_live = False → fail
        client = StubClient(snapshot=make_snapshot(account_id="U1234567"))
        config = FlexSyncConfig(
            token="t", query_id=1, base_url="https://...",
            account_id=None, poll_interval_seconds=0, max_wait_seconds=10,
            allow_live=False,
        )
        with session_factory() as session:
            result = run_readonly_sync(session, client=client, config=config)
            assert result.status == "failed"
            assert result.error_type == "LiveAccountRefusedError"

    def test_unknown_account_also_refused(self, session_factory, make_snapshot):
        # snapshot with weird account_id classified as unknown
        client = StubClient(snapshot=make_snapshot(account_id="FOO1"))
        config = FlexSyncConfig(
            token="t", query_id=1, base_url="https://...",
            account_id=None, poll_interval_seconds=0, max_wait_seconds=10,
            allow_live=False,
        )
        with session_factory() as session:
            result = run_readonly_sync(session, client=client, config=config)
            assert result.status == "failed"
            assert result.error_type == "LiveAccountRefusedError"

    def test_account_mismatch(self, session_factory, make_snapshot):
        client = StubClient(snapshot=make_snapshot(account_id="DU1"))
        config = FlexSyncConfig(
            token="t", query_id=1, base_url="https://...",
            account_id="DU2",  # different
            poll_interval_seconds=0, max_wait_seconds=10,
            allow_live=False,
        )
        with session_factory() as session:
            result = run_readonly_sync(session, client=client, config=config)
            assert result.status == "failed"
            assert result.error_type == "AccountMismatchError"

    def test_reference_code_preserved_on_failure(self, session_factory):
        """When SendRequest succeeded but GetStatement timed out, reference_code
        must end up in broker_sync_run.context."""
        # ... (use a stub that raises FlexReportTimeoutError(ref="ABC"))
```

(The subagent should match the existing fixture style in the file; details are file-specific.)

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/broker/test_readonly_sync.py -v
git add marketpulse/broker/readonly_sync.py tests/broker/test_readonly_sync.py
git commit -m "feat(7a-flex): FlexSyncConfig orchestration with live/mismatch brakes + reference_code threading"
```

---

### Task 7: CLI rewrite

**Files:**
- Modify: `scripts/sync_ibkr_readonly.py`
- Modify: `tests/broker/test_sync_cli.py`

- [ ] **Step 1: Rewrite `scripts/sync_ibkr_readonly.py`**

Full replacement:

```python
"""Run one IBKR Flex read-only broker snapshot sync.

Phase 7a-Flex: pulls broker truth via IBKR's Flex Web Service. Configure
IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID in your environment (see
docs/operations/ibkr-readonly-sync-runbook.md for the IBKR Portal setup).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path[:0] = [str(Path(__file__).resolve().parent.parent)]

from marketpulse.broker.flex_client import FlexClient  # noqa: E402
from marketpulse.broker.readonly_sync import (  # noqa: E402
    FlexSyncConfig,
    run_readonly_sync,
)
from marketpulse.broker.types import SyncResult  # noqa: E402
from marketpulse.config import get_settings  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", help="override IBKR_FLEX_TOKEN")
    parser.add_argument("--query-id", type=int, help="override IBKR_FLEX_QUERY_ID")
    parser.add_argument("--base-url", help="override IBKR_FLEX_BASE_URL")
    parser.add_argument("--account-id", help="override IBKR_ACCOUNT_ID")
    parser.add_argument("--poll-interval-seconds", type=int)
    parser.add_argument("--max-wait-seconds", type=int)
    parser.add_argument("--db-url")
    return parser


def _config(args: argparse.Namespace) -> tuple[FlexSyncConfig, str]:
    settings = get_settings()
    token = args.token or settings.ibkr_flex_token
    query_id = args.query_id if args.query_id is not None else settings.ibkr_flex_query_id
    if not token:
        raise SystemExit("IBKR_FLEX_TOKEN is not set (or --token not given)")
    if not query_id:
        raise SystemExit("IBKR_FLEX_QUERY_ID is not set (or --query-id not given)")
    return (
        FlexSyncConfig(
            token=token,
            query_id=query_id,
            base_url=args.base_url or settings.ibkr_flex_base_url,
            account_id=args.account_id or settings.ibkr_account_id or None,
            poll_interval_seconds=(
                args.poll_interval_seconds
                if args.poll_interval_seconds is not None
                else settings.ibkr_flex_poll_interval_seconds
            ),
            max_wait_seconds=(
                args.max_wait_seconds
                if args.max_wait_seconds is not None
                else settings.ibkr_flex_max_wait_seconds
            ),
            allow_live=settings.ibkr_allow_live,
        ),
        args.db_url or settings.database_url,
    )


def _run(args: argparse.Namespace) -> SyncResult:
    config, db_url = _config(args)
    client = FlexClient(
        token=config.token,
        query_id=config.query_id,
        account_id=config.account_id,
        base_url=config.base_url,
        poll_interval_seconds=config.poll_interval_seconds,
        max_wait_seconds=config.max_wait_seconds,
    )
    engine = create_engine(db_url)
    with Session(engine) as session:
        result = run_readonly_sync(session, client=client, config=config, now=datetime.now(UTC))
        session.commit()
        return result


def _print_result(result: SyncResult) -> None:
    print(f"sync_run_id: {result.sync_run_id}")
    print(f"broker: {result.broker}")
    print(f"broker_environment: {result.broker_environment}")
    print(f"account: {result.account_id or 'unknown'}")
    print(f"transport: {result.transport}")
    print(f"endpoint: {result.endpoint}")
    print(f"query_id: {result.query_id}")
    if result.reference_code:
        print(f"reference_code: {result.reference_code}")
    print(f"status: {result.status}")
    if result.status == "completed":
        print(f"account snapshots: {result.account_snapshots}")
        print(f"cash rows: {result.cash_rows}")
        print(f"positions: {result.positions}")
        print(f"open orders: {result.open_orders} (not available via Flex Activity)")
        print(f"executions: {result.executions}")
    else:
        print(f"error_type: {result.error_type}")
        print(f"error_message: {result.error_message}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = _run(args)
    _print_result(result)
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Update `tests/broker/test_sync_cli.py`**

Rewrite to use the new flags. The general shape:

```python
def test_cli_happy_path(monkeypatch, capsys):
    # ... arrange mocked FlexClient that returns a known snapshot
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "123")
    # ... run CLI, assert output contains "transport: flex", "query_id: 123"

def test_cli_missing_token_exits(monkeypatch):
    monkeypatch.delenv("IBKR_FLEX_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="IBKR_FLEX_TOKEN"):
        ...
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/broker/test_sync_cli.py -v
git add scripts/sync_ibkr_readonly.py tests/broker/test_sync_cli.py
git commit -m "feat(7a-flex): CLI uses Flex transport with --token/--query-id/--max-wait-seconds"
```

---

### Task 8: Remove Gateway settings

**Files:**
- Modify: `marketpulse/config.py`

- [ ] **Step 1: Delete the Gateway settings fields**

In `marketpulse/config.py`, delete:
- `ibkr_host`
- `ibkr_port`
- `ibkr_client_id`
- `ibkr_connect_timeout_seconds`

Keep `ibkr_account_id` (still useful — Flex uses it) and `ibkr_allow_live` (still useful — Flex brake uses it).

- [ ] **Step 2: Run full suite to catch any stragglers**

```bash
uv run pytest tests/ -x -q
```

Expected: clean (any failures here mean someone still references `settings.ibkr_host` etc; grep for `ibkr_host`, `ibkr_port`, `ibkr_client_id`, `ibkr_connect_timeout_seconds` in `marketpulse/` and `tests/` and clean them up).

- [ ] **Step 3: Commit**

```bash
git add marketpulse/config.py
git commit -m "chore(7a-flex): drop Gateway-only settings (host/port/client_id/connect_timeout)"
```

---

### Task 9: Compose + .env.example + DEPLOY.md

**Files:**
- Modify: `docker-compose.cn.yml`
- Modify: `docker-compose.prod.yml`
- Modify: `.env.example`
- Modify: `DEPLOY.md` (audit for gateway/VNC mentions)

- [ ] **Step 1: Rewrite Phase 7a section of `.env.example`**

Replace the entire Phase 7a block (lines ~57-108, the IBKR_USERNAME through MP_IBKR_ALLOW_LIVE region) with:

```
# ─────────────────────────────────────────────────────────────────────────
# Phase 7a-Flex — IBKR read-only broker truth capture via Flex Web Service
# ─────────────────────────────────────────────────────────────────────────
# No Gateway sidecar required. Snapshots are pulled via IBKR's official
# Flex Web Service (HTTPS+XML). See docs/operations/ibkr-readonly-sync-runbook.md
# for the one-time IBKR Portal setup (create an Activity Flex Query + token).

# Required to enable Flex sync. Token is a 64-char secret from IBKR Portal →
# Reports → Flex Queries → Configure (gear icon) → Token Renewal.
IBKR_FLEX_TOKEN=

# Activity Flex Query ID (integer) from IBKR Portal → Reports → Flex Queries.
# Create one query that includes Account Information, Cash Report, Open Positions,
# and Trades sections.
IBKR_FLEX_QUERY_ID=

# Account ID (DU<digits> for paper, U<digits> for live). Recommended; required if
# your Flex Query returns multiple accounts.
IBKR_ACCOUNT_ID=

# Live-account safety brake. The sync refuses to capture any non-paper account
# unless this is explicitly set to true. "unknown" account classifications
# also trip this brake (defense in depth).
MP_IBKR_ALLOW_LIVE=false

# Flex tunables (defaults are sensible; uncomment to override):
# IBKR_FLEX_POLL_INTERVAL_SECONDS=5
# IBKR_FLEX_MAX_WAIT_SECONDS=60
# IBKR_FLEX_BASE_URL=https://gdcdyn.interactivebrokers.com/Universal/servlet
```

Also remove `ib-gateway` from the `NO_PROXY=` default list at line 55.

- [ ] **Step 2: Rewrite `docker-compose.cn.yml`**

- Delete the entire `ib-gateway:` service block (lines ~107-154).
- Remove from `marketpulse.environment`: `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`, `IBKR_CONNECT_TIMEOUT_SECONDS` (keep `IBKR_ACCOUNT_ID`, `MP_IBKR_ALLOW_LIVE`).
- Add to `marketpulse.environment`: `IBKR_FLEX_TOKEN`, `IBKR_FLEX_QUERY_ID`, `IBKR_FLEX_POLL_INTERVAL_SECONDS`, `IBKR_FLEX_MAX_WAIT_SECONDS`, `IBKR_FLEX_BASE_URL`.
- Remove `ib-gateway` from the `NO_PROXY` default value.
- Remove any `depends_on: [ib-gateway]` if present (check the marketpulse service block).
- Remove the comment block above the deleted `ib-gateway` service.

Resulting env block addition:
```yaml
      IBKR_FLEX_TOKEN: ${IBKR_FLEX_TOKEN:-}
      IBKR_FLEX_QUERY_ID: ${IBKR_FLEX_QUERY_ID:-}
      IBKR_FLEX_BASE_URL: ${IBKR_FLEX_BASE_URL:-https://gdcdyn.interactivebrokers.com/Universal/servlet}
      IBKR_FLEX_POLL_INTERVAL_SECONDS: ${IBKR_FLEX_POLL_INTERVAL_SECONDS:-5}
      IBKR_FLEX_MAX_WAIT_SECONDS: ${IBKR_FLEX_MAX_WAIT_SECONDS:-60}
      IBKR_ACCOUNT_ID: ${IBKR_ACCOUNT_ID:-}
      MP_IBKR_ALLOW_LIVE: ${MP_IBKR_ALLOW_LIVE:-false}
```

- [ ] **Step 3: Repeat for `docker-compose.prod.yml`**

Same edits as cn.yml. Note: prod.yml has the `IB_GATEWAY_VNC_BIND` port binding inside the ib-gateway block — gone with the block.

- [ ] **Step 4: Audit `DEPLOY.md`**

```bash
grep -nE "ib-?gateway|gateway|TWS|VNC|ibapi|IBKR_USERNAME|IBKR_PASSWORD|TWS_USERID" DEPLOY.md
```

For each match, replace gateway-era content with a one-line pointer to the runbook:

> Phase 7a IBKR broker truth capture: see `docs/operations/ibkr-readonly-sync-runbook.md`.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.cn.yml docker-compose.prod.yml .env.example DEPLOY.md
git commit -m "ops(7a-flex): remove ib-gateway sidecar; switch compose + .env to Flex settings"
```

---

### Task 10: Runbook rewrite

**Files:**
- Rewrite: `docs/operations/ibkr-readonly-sync-runbook.md`
- Add header pointer: `docs/superpowers/specs/2026-05-23-phase-7a-ibkr-readonly-sync-design.md`

- [ ] **Step 1: Add forward-pointer to the old 7a spec**

At the top of `docs/superpowers/specs/2026-05-23-phase-7a-ibkr-readonly-sync-design.md`, insert immediately after the title:

```markdown
> **SUPERSEDED 2026-05-24:** the IB Gateway sidecar / ibapi transport in this spec
> was replaced by IBKR Flex Web Service in
> [Phase 7a-Flex](2026-05-24-phase-7a-flex-readonly-sync-design.md). This document
> is kept for historical reference; do not implement from it.
```

- [ ] **Step 2: Rewrite the runbook**

Full replacement of `docs/operations/ibkr-readonly-sync-runbook.md`:

```markdown
# IBKR Read-Only Sync Runbook (Phase 7a-Flex)

Phase 7a-Flex captures IBKR broker truth via the official Flex Web Service
into the append-only `broker_*` snapshot tables. No daemon, no Gateway
container, no VNC, no 2FA at request time.

## Preconditions

- IBKR account with paper trading enabled (DU<digits>) or live account if `MP_IBKR_ALLOW_LIVE=true`.
- Activity Flex Query created in IBKR Portal (one-time setup, below).
- Flex Token issued and recorded (one-time setup, below).
- Outbound HTTPS to `gdcdyn.interactivebrokers.com` reachable from MarketPulse runtime.

## One-time setup: IBKR Portal

### 1. Create the Activity Flex Query

1. Log in to <https://www.interactivebrokers.com> → **Reports** → **Flex Queries**.
2. **Activity Flex Query** → "Create" (or pencil-edit an existing one).
3. Name it `MarketPulse_7a_ReadOnly_Snapshot`.
4. Period: `Last Business Day` (or `Today`).
5. Format: `XML`, Date format `yyyy-MM-dd`, Time format `HH:mm:ss`.
6. **Sections** — tick exactly these (others are optional, see "Section drift"):
   - **Account Information** (REQUIRED)
   - **Cash Report** → tick "All currencies"
   - **Open Positions**
   - **Trades** → tick at least "Executions"
7. Save. Note the **Query ID** (a 6-digit integer).

### 2. Issue a Flex Token

1. Reports → Flex Queries → top right gear → **Token Renewal** (or "Get Current Token").
2. Generate token. **Save it in a password manager** — IBKR does not let you re-display existing tokens.
3. Tokens do not expire on a fixed schedule but can be revoked manually.

### 3. Populate `.env` / Portainer env

```env
IBKR_FLEX_TOKEN=<64-char token>
IBKR_FLEX_QUERY_ID=<6-digit query id>
IBKR_ACCOUNT_ID=DUxxxxxxx
MP_IBKR_ALLOW_LIVE=false
```

If using Portainer, **escape `$` as `$$`** in any field — docker-compose
variable substitution will silently truncate otherwise. (Tokens are
hexadecimal so usually unaffected; this is a generic warning.)

## Manual smoke

```bash
uv run python scripts/sync_ibkr_readonly.py
```

Successful output:

```text
sync_run_id: 1
broker: IBKR
broker_environment: paper
account: DU1234567
transport: flex
endpoint: https://gdcdyn.interactivebrokers.com/Universal/servlet
query_id: 123456
reference_code: 1234567890
status: completed
account snapshots: 1
cash rows: 2
positions: 5
open orders: 0 (not available via Flex Activity)
executions: 3
```

Failed output:

```text
sync_run_id: 2
broker: IBKR
broker_environment: paper
account: unknown
transport: flex
endpoint: https://gdcdyn.interactivebrokers.com/Universal/servlet
query_id: 123456
reference_code: 9876543210
status: failed
error_type: FlexReportTimeoutError
error_message: Flex report not ready after 60s
```

Note `reference_code` is printed on every run where `SendRequest` succeeded
— even on failure. To manually re-fetch:

```bash
curl "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement?t=<TOKEN>&q=<REFERENCE_CODE>&v=3"
```

## Error diagnosis

| error_type | Meaning | Operator action |
|---|---|---|
| `FlexHttpError` | DNS / TLS / 5xx / timeout at transport layer | Check network reachability; retry; check IBKR status page |
| `FlexAuthError` | Token rejected (code 1003/1011/1012) | Re-issue token in Portal; update env |
| `FlexSendRequestError` | SendRequest XML had non-auth error | Check Query ID; check token has access to that query |
| `FlexReportTimeoutError` | Polling exhausted `IBKR_FLEX_MAX_WAIT_SECONDS` | Raise `IBKR_FLEX_MAX_WAIT_SECONDS` or wait and re-run with reference code |
| `FlexStatementError` | GetStatement returned error after ready (e.g. expired reference) | Re-run from scratch |
| `FlexParseError` | XML malformed or Account section missing | Check Query in Portal has Account Information ticked |
| `FlexAccountMismatchError` | Report contains different account than `IBKR_ACCOUNT_ID` | Either unset `IBKR_ACCOUNT_ID` or fix it |
| `LiveAccountRefusedError` | Account is not classified `paper` | Set `MP_IBKR_ALLOW_LIVE=true` if intentional |
| `AccountMismatchError` | Configured `IBKR_ACCOUNT_ID` ≠ snapshot's account | Same as `FlexAccountMismatchError` |

## Inspecting recent runs

```bash
sqlite3 data/marketpulse.db <<'SQL'
SELECT id, started_at, completed_at, broker_environment, account_id,
       status, error_type, json_extract(context, '$.reference_code') AS ref
FROM broker_sync_run
ORDER BY id DESC
LIMIT 5;
SQL
```

## Section drift

The Flex Query is configured in IBKR Portal, not in code. If you uncheck a
section:

- **Account Information**: missing → `FlexParseError`. Fix by re-ticking.
- **Cash Report / Open Positions / Trades**: missing → 0 rows recorded with no error. Intentional.

The parser will not hard-fail on missing optional sections, so you can
narrow the Query to just Account + Positions for example without breaking
the sync.

## What 7a-Flex never does

- No order placement / modification / cancellation.
- No realtime quote streaming.
- No scheduler or daemon — operator runs the CLI manually or via cron.
- No web-triggered sync.
- No writes to `paper_*` tables.
- No paper-vs-broker reconciliation.
- No open-order capture (Flex Activity reports do not include working orders).

## Phase 7b/7c

The Gateway-based write path (order placement, real-time book) is Phase 7b
and uses a separately-chosen transport (likely ibeam + Client Portal Web API
or IBKR's TWS API via a re-introduced sidecar). Phase 7a-Flex does not
constrain that choice.
```

- [ ] **Step 3: Commit**

```bash
git add docs/operations/ibkr-readonly-sync-runbook.md docs/superpowers/specs/2026-05-23-phase-7a-ibkr-readonly-sync-design.md
git commit -m "docs(7a-flex): runbook rewritten for Flex; old 7a spec marked superseded"
```

---

### Task 11: Final integration sweep + PR push

- [ ] **Step 1: Full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all green. If any failures, fix in place, do not relax tests.

- [ ] **Step 2: Lint + type**

```bash
uv run ruff check .
uv run ruff format --check .
# Optional: mypy / pyright if the project uses one
```

- [ ] **Step 3: Manual import smoke**

```bash
uv run python -c "from marketpulse.broker.flex_client import FlexClient; from marketpulse.broker.readonly_sync import FlexSyncConfig, run_readonly_sync; from marketpulse.broker.types import SyncResult, classify_broker_environment_from_account_id; print('imports OK')"
```

- [ ] **Step 4: CLI help smoke**

```bash
uv run python scripts/sync_ibkr_readonly.py --help
```

Expected: help text shows `--token`, `--query-id`, `--poll-interval-seconds`, `--max-wait-seconds`, `--account-id`, `--base-url`, `--db-url`.

- [ ] **Step 5: Final architecture guard**

```bash
uv run pytest tests/architecture/ -v
```

- [ ] **Step 6: Branch push + PR**

```bash
git push -u origin plan/phase-7a-flex-readonly-sync
gh pr create --title "Phase 7a-Flex: replace IB Gateway sidecar with Flex Web Service" --body "$(cat <<'EOF'
## Summary
- Replaces the gnzsnz/ib-gateway sidecar transport with IBKR's official Flex Web Service (HTTPS+XML).
- New `marketpulse/broker/flex_client.py` implements `BrokerReadClient` Protocol; no daemon, no 2FA, no daily forced logout.
- `SyncResult` refactored to a Flex-shaped dataclass (`transport`/`endpoint`/`query_id`/`reference_code`).
- 18 lock points captured in the spec; 11 XML fixtures cover the parser; live-account brake is now account-id-based.
- DB schema unchanged. CLI name unchanged. Gateway sidecar + `ibapi` dep removed.

## Test Plan
- [ ] Full pytest suite green
- [ ] ruff clean
- [ ] CLI `--help` lists Flex flags
- [ ] Architecture guard: no `ibapi` import in production code
- [ ] Manual smoke against a real IBKR paper account (run after merge in Portainer)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 7: Operator NAS deployment notes (for after merge)**

Document at PR description bottom or in the runbook section:
1. Portainer → Stacks → marketpulse → Update.
2. Add env vars: `IBKR_FLEX_TOKEN`, `IBKR_FLEX_QUERY_ID`, `IBKR_ACCOUNT_ID`, keep `MP_IBKR_ALLOW_LIVE=false`.
3. Remove env vars: `IBKR_USERNAME`, `IBKR_PASSWORD`, `IBKR_TRADING_MODE`, `IBKR_READ_ONLY_API`, `IB_GATEWAY_VNC_BIND`, `VNC_SERVER_PASSWORD`, `EXISTING_SESSION_DETECTED_ACTION`, `IB_GATEWAY_IMAGE`, `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`, `IBKR_CONNECT_TIMEOUT_SECONDS`.
4. Update the stack. The `ib-gateway` container disappears; only `marketpulse` remains.
5. Run `docker exec marketpulse uv run python scripts/sync_ibkr_readonly.py` — confirm "status: completed" and broker_* row counts > 0.
