# Phase 6h Paper Trading Ops Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only operator tooling and docs so paper trading can be deployed, smoke-tested, diagnosed, and accepted without reading source code.

**Architecture:** Implement three standalone scripts under `scripts/` and two operator docs under `docs/operations/`. Scripts are snapshot checks only: they reuse existing 6f query models, existing notifier factory, existing price provider abstractions, and never write paper trading state.

**Tech Stack:** Python 3.12, SQLAlchemy, FastAPI TestClient-compatible HTTP flows via `httpx`, existing `marketpulse.trading.query_models`, existing `marketpulse.alerts.notifier`, existing `YFinancePriceProvider`.

---

## File Structure

- Create: `scripts/_paper_ops_common.py`
  - Shared CLI helpers: project-root import setup, DB URL resolution, SQLAlchemy session creation, paper table row counts, stable result printing.
- Create: `scripts/check_paper_trading_health.py`
  - Read-only DB health snapshot. Uses `load_paper_trading_dashboard(...)` for COW/status/stuck/kill switch semantics. Adds price provider smoke against most recent completed NY trading day.
- Create: `scripts/smoke_paper_trading_ops.py`
  - HTTP deployment smoke. Uses normal `/login` form flow, preserves cookies, checks `/lab/paper-trading` markers and read-only guard.
- Create: `scripts/smoke_notifications.py`
  - Notification config/send smoke. Default is config-only. `--send` requires `--confirm-send` and emits a fixed smoke title.
- Create: `docs/operations/paper-trading-runbook.md`
  - Operator-first diagnosis and mitigation guide.
- Create: `docs/operations/paper-trading-acceptance-checklist.md`
  - Release gate checklist for deploy and next real tick acceptance.
- Create: `tests/ops/test_paper_trading_ops_scripts.py`
  - CLI behavior, health states, HTTP smoke, notification smoke, no-mutation runtime guards.
- Create: `tests/architecture/test_phase6h_no_mutation.py`
  - Static source guard for scripts.

---

## Task 1: Shared Ops Script Helpers

**Files:**
- Create: `scripts/_paper_ops_common.py`
- Test: `tests/ops/test_paper_trading_ops_scripts.py`

- [ ] **Step 1: Write failing tests for DB URL resolution and row-count helper**

Add the test file:

```python
"""Phase 6h ops hardening script tests."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_ops_common_resolves_db_url_from_arg_or_env(monkeypatch):
    from scripts._paper_ops_common import resolve_db_url

    monkeypatch.delenv("MARKETPULSE_DB_URL", raising=False)
    assert resolve_db_url("sqlite:///explicit.db") == "sqlite:///explicit.db"
    assert resolve_db_url(None) == "sqlite:///./marketpulse.db"

    monkeypatch.setenv("MARKETPULSE_DB_URL", "sqlite:///env.db")
    assert resolve_db_url(None) == "sqlite:///env.db"


def test_ops_common_counts_paper_tables(tmp_path):
    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperCashLedger
    from scripts._paper_ops_common import count_paper_tables

    engine = create_engine(f"sqlite:///{tmp_path / 'ops.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(PaperCashLedger(
            timestamp=datetime(2026, 5, 23, tzinfo=UTC),
            delta=100,
            reason="INITIAL_DEPOSIT",
            balance_after=100,
        ))
        session.commit()

        counts = count_paper_tables(session)

    assert counts["paper_order"] == 0
    assert counts["paper_fill"] == 0
    assert counts["paper_position"] == 0
    assert counts["paper_cash_ledger"] == 1
    assert counts["paper_audit_event"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/ops/test_paper_trading_ops_scripts.py::test_ops_common_resolves_db_url_from_arg_or_env tests/ops/test_paper_trading_ops_scripts.py::test_ops_common_counts_paper_tables -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts._paper_ops_common'`.

- [ ] **Step 3: Implement shared helper**

Create `scripts/_paper_ops_common.py`:

```python
"""Shared helpers for Phase 6h paper-trading ops scripts.

Read-only utilities only. This module must not mutate paper trading state.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

# Allow direct script execution via commands like
# `uv run python scripts/check_paper_trading_health.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketpulse.db.models import (  # noqa: E402
    PaperAuditEvent,
    PaperCashLedger,
    PaperFill,
    PaperOrder,
    PaperPosition,
)

DEFAULT_DB_URL = "sqlite:///./marketpulse.db"
PAPER_TABLE_MODELS = {
    "paper_order": PaperOrder,
    "paper_fill": PaperFill,
    "paper_position": PaperPosition,
    "paper_cash_ledger": PaperCashLedger,
    "paper_audit_event": PaperAuditEvent,
}


def resolve_db_url(db_url: str | None) -> str:
    return db_url or os.getenv("MARKETPULSE_DB_URL", DEFAULT_DB_URL)


@contextmanager
def session_from_url(db_url: str) -> Iterator[Session]:
    engine = create_engine(db_url)
    with Session(engine) as session:
        yield session


def count_paper_tables(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, model in PAPER_TABLE_MODELS.items():
        counts[name] = int(session.execute(select(func.count(model.id))).scalar() or 0)
    return counts
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/ops/test_paper_trading_ops_scripts.py::test_ops_common_resolves_db_url_from_arg_or_env tests/ops/test_paper_trading_ops_scripts.py::test_ops_common_counts_paper_tables -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/_paper_ops_common.py tests/ops/test_paper_trading_ops_scripts.py
git commit -m "feat(phase-6h-T1): add paper ops script helpers"
```

---

## Task 2: Paper Trading Health Check CLI

**Files:**
- Create: `scripts/check_paper_trading_health.py`
- Modify: `tests/ops/test_paper_trading_ops_scripts.py`

- [ ] **Step 1: Write failing tests for health CLI states**

Append:

```python
from datetime import UTC, datetime, timedelta


def test_health_cli_fresh_db_is_healthy(tmp_path, capsys):
    from marketpulse.db.base import Base
    from scripts.check_paper_trading_health import main

    db_path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    code = main([f"sqlite:///{db_path}", "--skip-price-smoke"])

    out = capsys.readouterr().out
    assert code == 0
    assert "System Status: Healthy" in out
    assert "No paper tick has completed yet" in out


def test_health_cli_attention_for_price_unavailable_three_plus(tmp_path, capsys):
    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperAuditEvent
    from scripts.check_paper_trading_health import main

    db_path = tmp_path / "attention.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        start = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
        session.add(PaperAuditEvent(
            timestamp=start,
            event_type="TICK_COMPLETED",
            reason="",
            context={"tick_date": "2026-05-23", "status": "completed"},
        ))
        session.add(PaperAuditEvent(
            timestamp=start + timedelta(minutes=1),
            event_type="PRICE_UNAVAILABLE",
            reason="no_price",
            context={"position_id": 7, "ticker": "AAPL", "attempt_count": 3},
        ))
        session.commit()

    code = main([f"sqlite:///{db_path}", "--skip-price-smoke"])

    out = capsys.readouterr().out
    assert code == 1
    assert "System Status: Attention" in out
    assert "PRICE_UNAVAILABLE" in out
    assert "AAPL" in out


def test_health_cli_db_failure_returns_2(capsys):
    from scripts.check_paper_trading_health import main

    code = main(["sqlite:////definitely/missing/path/marketpulse.db"])

    out = capsys.readouterr().out
    assert code == 2
    assert "FAILED:" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/ops/test_paper_trading_ops_scripts.py -q -k "health_cli"
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.check_paper_trading_health'`.

- [ ] **Step 3: Implement health CLI**

Create `scripts/check_paper_trading_health.py`:

```python
"""Phase 6h paper-trading health snapshot.

Read-only. Uses 6f query models for operational status semantics.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Allow direct script execution from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._paper_ops_common import resolve_db_url, session_from_url

from marketpulse.data.yfinance_client import YFinanceClient
from marketpulse.trading.calendar import NY, NYTradingCalendar
from marketpulse.trading.price_provider import YFinancePriceProvider
from marketpulse.trading.query_models import load_paper_trading_dashboard


def _status_value(status: object) -> str:
    value = getattr(status, "value", status)
    return str(value)


def _generated_at_label(dashboard) -> str:
    label = getattr(dashboard, "generated_at_label", None)
    if label is not None:
        return str(label)
    return f"Generated at {dashboard.generated_at.astimezone(NY):%H:%M NY}"


def _latest_completed_ny_trading_day(now: datetime | None = None):
    now_utc = now or datetime.now(UTC)
    ny_now = now_utc.astimezone(NY)
    candidate = ny_now.date()
    if ny_now.hour < 16:
        candidate = candidate - timedelta(days=1)
    cal = NYTradingCalendar()
    while not cal.is_business_day(candidate):
        candidate = candidate - timedelta(days=1)
    return candidate


def _price_smoke(*, ticker: str) -> tuple[str, str]:
    on_date = _latest_completed_ny_trading_day()
    provider = YFinancePriceProvider(client=YFinanceClient())
    close = provider.close_on_date(ticker=ticker, on_date=on_date)
    if close is None:
        return "Attention", f"Price smoke unavailable: {ticker} close for {on_date}"
    return (
        "Healthy",
        f"Price smoke OK: {ticker} {close.price} on {close.price_date} ({close.source})",
    )


def _dashboard_to_dict(dashboard) -> dict:
    data = asdict(dashboard)
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_url", nargs="?", help="SQLAlchemy DB URL")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--skip-price-smoke", action="store_true")
    parser.add_argument("--price-ticker", default="SPY")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        db_url = resolve_db_url(args.db_url)
        with session_from_url(db_url) as session:
            dashboard = load_paper_trading_dashboard(session)
        price_status = "Skipped"
        price_detail = "Price smoke skipped"
        if not args.skip_price_smoke:
            price_status, price_detail = _price_smoke(ticker=args.price_ticker)
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 2

    effective_status = _status_value(dashboard.system_status)
    if price_status == "Attention" and effective_status == "Healthy":
        effective_status = "Attention"

    if args.as_json:
        payload = _dashboard_to_dict(dashboard)
        payload["effective_status"] = effective_status
        payload["price_smoke"] = {"status": price_status, "detail": price_detail}
        print(json.dumps(payload, default=str, indent=2, sort_keys=True))
    else:
        print(f"System Status: {effective_status}")
        print(dashboard.current_operational_window.label)
        print(_generated_at_label(dashboard))
        print(f"Latest Tick: {dashboard.health.latest_tick_status or 'none'}")
        print(f"Cash Balance: {dashboard.health.cash_balance}")
        print(f"Open Positions: {dashboard.health.open_positions_count}")
        print(f"Kill Switch: {dashboard.health.kill_switch_state}")
        print(price_detail)
        if dashboard.critical_events.status == "ok":
            events = dashboard.critical_events.data or []
            if events:
                print("Operational Events:")
                for event in events:
                    ticker = f" {event.ticker}" if event.ticker else ""
                    print(f"- {event.severity}: {event.event_type}{ticker} {event.detail}")
            else:
                print(dashboard.critical_events.empty_message)
        else:
            print(f"Degraded: {dashboard.critical_events.error_title}")

    return 0 if effective_status == "Healthy" else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/ops/test_paper_trading_ops_scripts.py -q -k "health_cli"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_paper_trading_health.py tests/ops/test_paper_trading_ops_scripts.py
git commit -m "feat(phase-6h-T2): add paper trading health check"
```

---

## Task 3: HTTP Deployment Smoke CLI

**Files:**
- Create: `scripts/smoke_paper_trading_ops.py`
- Modify: `tests/ops/test_paper_trading_ops_scripts.py`

- [ ] **Step 1: Write failing tests for route smoke**

Append:

```python
def test_route_smoke_requires_password(capsys):
    from scripts.smoke_paper_trading_ops import main

    code = main(["--base-url", "http://example.test"])

    out = capsys.readouterr().out
    assert code == 2
    assert "password" in out.lower()


def test_route_smoke_success_with_mock_transport(monkeypatch, capsys):
    import httpx
    from scripts import smoke_paper_trading_ops as smoke

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/lab/paper-trading":
            if "session=" not in request.headers.get("cookie", ""):
                return httpx.Response(303, headers={"location": "/login"})
            return httpx.Response(
                200,
                text=(
                    "Paper Trading · Operations System Status Generated at "
                    "Critical Events Positions Orders & Fills Audit Timeline"
                ),
            )
        if request.method == "POST" and request.url.path == "/login":
            return httpx.Response(303, headers={"Set-Cookie": "session=abc; Path=/"})
        if request.method == "POST" and request.url.path == "/lab/paper-trading":
            return httpx.Response(405)
        return httpx.Response(404)

    monkeypatch.setattr(
        smoke,
        "_client",
        lambda timeout: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False, timeout=timeout),
    )

    code = smoke.main(["--base-url", "http://example.test", "--password", "dev"])

    out = capsys.readouterr().out
    assert code == 0
    assert "OK: /lab/paper-trading smoke passed" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/ops/test_paper_trading_ops_scripts.py -q -k "route_smoke"
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.smoke_paper_trading_ops'`.

- [ ] **Step 3: Implement route smoke CLI**

Create `scripts/smoke_paper_trading_ops.py`:

```python
"""Phase 6h deployment smoke for /lab/paper-trading."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

# Allow direct script execution from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REQUIRED_MARKERS = (
    "Paper Trading · Operations",
    "System Status",
    "Generated at",
    "Critical Events",
    "Positions",
    "Orders & Fills",
    "Audit Timeline",
)
FORBIDDEN_MARKERS = (
    "Force Close",
    "Replay",
    "Retry",
    "Kill Switch Toggle",
    'type="submit"',
)


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(follow_redirects=False, timeout=timeout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--password")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    return parser


def _fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    password = args.password or os.getenv("MARKETPULSE_SMOKE_PASSWORD")
    if not password:
        print("ERROR: password required via --password or MARKETPULSE_SMOKE_PASSWORD")
        return 2

    base_url = args.base_url.rstrip("/")
    try:
        with _client(args.timeout_seconds) as client:
            unauth = client.get(f"{base_url}/lab/paper-trading")
            if unauth.status_code not in {302, 303} or "/login" not in unauth.headers.get("location", ""):
                return _fail("unauthenticated /lab/paper-trading did not redirect to /login")

            post_route = client.post(f"{base_url}/lab/paper-trading")
            if post_route.status_code != 405:
                return _fail("POST /lab/paper-trading did not return 405")

            login = client.post(f"{base_url}/login", data={"password": password})
            if login.status_code not in {302, 303}:
                return _fail("login failed")

            page = client.get(f"{base_url}/lab/paper-trading")
            if page.status_code != 200:
                return _fail(f"authenticated /lab/paper-trading returned {page.status_code}")

            missing = [marker for marker in REQUIRED_MARKERS if marker not in page.text]
            if missing:
                return _fail(f"missing marker(s): {', '.join(missing)}")

            forbidden = [marker for marker in FORBIDDEN_MARKERS if marker in page.text]
            if forbidden:
                return _fail(f"control-plane marker(s) present: {', '.join(forbidden)}")
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 2

    print("OK: /lab/paper-trading smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/ops/test_paper_trading_ops_scripts.py -q -k "route_smoke"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_paper_trading_ops.py tests/ops/test_paper_trading_ops_scripts.py
git commit -m "feat(phase-6h-T3): add paper trading route smoke"
```

---

## Task 4: Notification Smoke CLI

**Files:**
- Create: `scripts/smoke_notifications.py`
- Modify: `tests/ops/test_paper_trading_ops_scripts.py`

- [ ] **Step 1: Write failing tests for notification smoke**

Append:

```python
def test_notification_smoke_default_does_not_send(monkeypatch, capsys):
    from scripts import smoke_notifications

    sent = []

    class CapturingNotifier:
        def send(self, title, body, url=None):
            sent.append((title, body, url))
            return True

    monkeypatch.setattr(smoke_notifications, "get_notifier_from_settings", lambda settings: CapturingNotifier())

    code = smoke_notifications.main([])

    out = capsys.readouterr().out
    assert code == 0
    assert "config OK" in out
    assert sent == []


def test_notification_smoke_send_requires_confirm(capsys):
    from scripts.smoke_notifications import main

    code = main(["--send"])

    out = capsys.readouterr().out
    assert code == 2
    assert "--confirm-send" in out


def test_notification_smoke_send_uses_fixed_smoke_title(monkeypatch):
    from scripts import smoke_notifications

    sent = []

    class CapturingNotifier:
        def send(self, title, body, url=None):
            sent.append((title, body, url))
            return True

    monkeypatch.setattr(smoke_notifications, "get_notifier_from_settings", lambda settings: CapturingNotifier())

    code = smoke_notifications.main(["--send", "--confirm-send"])

    assert code == 0
    assert sent
    assert sent[0][0].startswith("SMOKE TEST — Paper Trading Notifications")
    assert "SMOKE TEST" in sent[0][1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/ops/test_paper_trading_ops_scripts.py -q -k "notification_smoke"
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.smoke_notifications'`.

- [ ] **Step 3: Implement notification smoke CLI**

Create `scripts/smoke_notifications.py`:

```python
"""Phase 6h notification smoke.

Default mode validates configuration only. Sending requires both --send and
--confirm-send.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

# Allow direct script execution from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketpulse.alerts.notifier import NoopNotifier, get_notifier_from_settings
from marketpulse.config import get_settings

SMOKE_TITLE = "SMOKE TEST — Paper Trading Notifications"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--confirm-send", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.send and not args.confirm_send:
        print("ERROR: --send requires --confirm-send")
        return 2

    try:
        settings = get_settings()
        notifier = get_notifier_from_settings(settings)
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1

    kind = settings.notifier_kind or "none"
    if isinstance(notifier, NoopNotifier):
        print(f"Notification config OK: kind={kind} resolves to NoopNotifier")
    else:
        print(f"Notification config OK: kind={kind}")

    if not args.send:
        print("No send attempted. Pass --send --confirm-send to emit smoke message.")
        return 0

    body = (
        "SMOKE TEST only. This is not a paper tick, fill, risk alert, "
        f"or trading event. Sent at {datetime.now(UTC).isoformat()}."
    )
    ok = notifier.send(SMOKE_TITLE, body, None)
    if not ok:
        print("FAIL: notifier.send returned false")
        return 1
    print("OK: notification smoke sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/ops/test_paper_trading_ops_scripts.py -q -k "notification_smoke"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_notifications.py tests/ops/test_paper_trading_ops_scripts.py
git commit -m "feat(phase-6h-T4): add notification smoke"
```

---

## Task 5: No-Mutation Guards

**Files:**
- Create: `tests/architecture/test_phase6h_no_mutation.py`
- Modify: `tests/ops/test_paper_trading_ops_scripts.py`

- [ ] **Step 1: Write failing static guard test**

Create `tests/architecture/test_phase6h_no_mutation.py`:

```python
"""Architecture guards for Phase 6h ops scripts."""

from __future__ import annotations

from pathlib import Path


PHASE6H_SCRIPTS = [
    Path("scripts/check_paper_trading_health.py"),
    Path("scripts/smoke_paper_trading_ops.py"),
    Path("scripts/smoke_notifications.py"),
]


def test_phase6h_scripts_do_not_use_sqlalchemy_mutation_apis():
    forbidden = (
        ".add(",
        ".merge(",
        ".delete(",
        ".execute(",
        "insert(",
        "update(",
        "delete(",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
    )
    offenders: list[str] = []
    for path in PHASE6H_SCRIPTS:
        src = path.read_text()
        for needle in forbidden:
            if needle in src:
                offenders.append(f"{path}:{needle}")

    assert offenders == []
```

- [ ] **Step 2: Add runtime row-count guard tests**

Append:

```python
def test_health_cli_does_not_mutate_paper_tables(tmp_path, monkeypatch):
    from marketpulse.db.base import Base
    from scripts._paper_ops_common import count_paper_tables
    from scripts.check_paper_trading_health import main

    db_path = tmp_path / "nomutate.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        before = count_paper_tables(session)

    code = main([f"sqlite:///{db_path}", "--skip-price-smoke"])

    with Session(engine) as session:
        after = count_paper_tables(session)
    assert code == 0
    assert after == before


def test_notification_smoke_default_does_not_mutate_paper_tables(tmp_path, monkeypatch):
    from marketpulse.db.base import Base
    from scripts._paper_ops_common import count_paper_tables
    from scripts.smoke_notifications import main

    db_path = tmp_path / "notify_nomutate.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    monkeypatch.setenv("MARKETPULSE_DB_URL", f"sqlite:///{db_path}")
    with Session(engine) as session:
        before = count_paper_tables(session)

    code = main([])

    with Session(engine) as session:
        after = count_paper_tables(session)
    assert code == 0
    assert after == before
```

- [ ] **Step 3: Run tests to verify behavior**

Run:

```bash
uv run pytest tests/architecture/test_phase6h_no_mutation.py tests/ops/test_paper_trading_ops_scripts.py -q -k "mutation or mutate"
```

Expected: PASS if prior tasks implemented safely. If static guard fails, remove mutation API usage from scripts rather than loosening the guard.

- [ ] **Step 4: Commit**

```bash
git add tests/architecture/test_phase6h_no_mutation.py tests/ops/test_paper_trading_ops_scripts.py
git commit -m "test(phase-6h-T5): guard ops scripts read-only"
```

---

## Task 6: Operator Runbook and Acceptance Checklist

**Files:**
- Create: `docs/operations/paper-trading-runbook.md`
- Create: `docs/operations/paper-trading-acceptance-checklist.md`

- [ ] **Step 1: Add operator runbook**

Create `docs/operations/paper-trading-runbook.md`:

```markdown
# Paper Trading Runbook

## Daily 30-Second Check

1. Open `/lab/paper-trading`.
2. Confirm `System Status`.
3. Confirm `Generated at` is current enough for the deployment context.
4. Check `Critical Events`.
5. Check `Positions`, especially `Exit Health`.

## Status Meanings

Healthy means current telemetry loaded and no active operational attention state is visible.

Attention means the system is visible, but an operator should inspect a current condition such as `PRICE_UNAVAILABLE`, scheduler gap, kill switch ON, or reprocessed tick.

Degraded means part of the dashboard or health query could not load. Treat it as a telemetry quality problem first, not proof that the trading engine is broken.

Failed is a script outcome, not a paper engine state. It means the check itself could not run.

## After-Deploy Smoke

Run:

```bash
MARKETPULSE_SMOKE_PASSWORD=dev uv run python scripts/smoke_paper_trading_ops.py --base-url http://127.0.0.1:8000
uv run python scripts/check_paper_trading_health.py sqlite:///./marketpulse.db --skip-price-smoke
uv run python scripts/smoke_notifications.py
```

Only run notification send smoke when intentionally testing the configured channel:

```bash
uv run python scripts/smoke_notifications.py --send --confirm-send
```

## PRICE_UNAVAILABLE

`PRICE_UNAVAILABLE_1` means the first exit-price lookup failed. Watch the next tick.

`PRICE_UNAVAILABLE_2` means the second consecutive lookup failed. Check provider health and ticker data availability.

`STUCK_3_PLUS` means the position has failed at least three exit attempts. Capture the dashboard, audit rows, ticker, position id, and provider smoke result before changing anything.

6h does not auto-close or repair stuck positions.

## Scheduler Gap

If a scheduler gap appears, capture:

- latest successful tick date;
- resume date;
- missed business days;
- deployment time;
- scheduler/container logs.

Do not retroactively replay missed allocation days unless a later spec explicitly allows it.

## Kill Switch ON

If kill switch is ON:

1. Confirm reason shown in dashboard or health output.
2. Confirm whether it is env override or audit flip.
3. Do not toggle it from 6f/6h tooling.
4. Follow the deployment/control-plane procedure for the environment.

## Notification Failure

Run config-only smoke first:

```bash
uv run python scripts/smoke_notifications.py
```

If config looks right, intentionally send:

```bash
uv run python scripts/smoke_notifications.py --send --confirm-send
```

The smoke title must begin with `SMOKE TEST — Paper Trading Notifications`.

## Price Provider Failure

Run health with price smoke enabled:

```bash
uv run python scripts/check_paper_trading_health.py sqlite:///./marketpulse.db
```

The price smoke checks the most recent completed NY trading day close for SPY by default. A failure is Attention, not automatic proof of engine corruption.

## Rollback / Mitigation

Safe mitigations:

- keep kill switch ON through existing deployment controls;
- pause unattended scheduler outside 6h if the deployment platform supports it;
- roll back the application image;
- capture DB/audit evidence before manual intervention.

6h never retries ticks, closes positions, edits audit rows, or repairs scheduler gaps.

## Evidence to Capture

- `/lab/paper-trading` screenshot;
- `check_paper_trading_health.py` output;
- relevant `paper_audit_event` rows;
- container/deployment logs;
- notification smoke output if notification delivery is suspect;
- price smoke output if exit pricing is suspect.

## Developer Appendix

Primary code entry points:

- `marketpulse.trading.query_models.load_paper_trading_dashboard`
- `marketpulse.trading.forward_engine.ForwardExecutionEngine`
- `marketpulse.trading.repository.Repository`
- `marketpulse.observability.paper_tick_notifier`
- `marketpulse.web.routes.lab`
```

- [ ] **Step 2: Add release gate checklist**

Create `docs/operations/paper-trading-acceptance-checklist.md`:

```markdown
# Paper Trading Acceptance Checklist

This checklist is required before enabling unattended daily paper ticks after a new deployment.

## Immediate Post-Deploy

- [ ] App container/process is running.
- [ ] Database migrations are current: `uv run alembic heads`.
- [ ] Deployed database revision is current: `uv run alembic current`.
- [ ] `/lab/paper-trading` route smoke passes:
  ```bash
  MARKETPULSE_SMOKE_PASSWORD=dev uv run python scripts/smoke_paper_trading_ops.py --base-url http://127.0.0.1:8000
  ```
- [ ] DB health snapshot runs:
  ```bash
  uv run python scripts/check_paper_trading_health.py sqlite:///./marketpulse.db --skip-price-smoke
  ```
- [ ] Notification config smoke runs:
  ```bash
  uv run python scripts/smoke_notifications.py
  ```
- [ ] Price provider smoke runs:
  ```bash
  uv run python scripts/check_paper_trading_health.py sqlite:///./marketpulse.db
  ```
- [ ] If price smoke reports Attention, classify it as external data/provider
      availability first, not an automatic deployment rollback.
- [ ] No unexpected `Degraded` state.
- [ ] No unexpected control-plane buttons are visible in `/lab/paper-trading`.

## Optional Notification Send

- [ ] If intentionally testing notification delivery, run:
  ```bash
  uv run python scripts/smoke_notifications.py --send --confirm-send
  ```
- [ ] Received title starts with `SMOKE TEST — Paper Trading Notifications`.

## Next Real Tick Acceptance

These items require the next real paper tick.

- [ ] Current Operational Window advanced or intentionally stayed unchanged.
- [ ] Latest tick status is understandable.
- [ ] 6g notification behavior matches tick outcome.
- [ ] Orders/Fills lifecycle is visible if orders were placed.
- [ ] Positions exit health is visible if positions are open.
- [ ] `PRICE_UNAVAILABLE` and recovery behavior, if present, matches audit rows.
- [ ] No scheduler gap unless an actual missed business day occurred.

## Accept / Reject Decision

- [ ] Accept unattended daily paper ticks.
- [ ] Keep unattended ticks disabled or kill switch ON.
- [ ] Roll back deployment.

## Sign-Off

- Date/time:
- Operator:
- App version / commit:
- DB URL or environment:
- Notes:
```

- [ ] **Step 3: Commit**

```bash
git add docs/operations/paper-trading-runbook.md docs/operations/paper-trading-acceptance-checklist.md
git commit -m "docs(phase-6h-T6): add paper trading ops runbook"
```

---

## Task 7: Final Verification and Deployed Smoke

**Files:**
- No production file changes unless verification finds issues.

- [ ] **Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/ops/test_paper_trading_ops_scripts.py tests/architecture/test_phase6h_no_mutation.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full verification**

Run:

```bash
uv run pytest
uv run ruff check .
uv run alembic heads
```

Expected:

- pytest: all tests pass.
- ruff: `All checks passed!`
- alembic: single head, currently `0011 (head)`.

- [ ] **Step 3: Manual local smoke**

Start the app:

```bash
uv run uvicorn marketpulse.web.main:app --host 127.0.0.1 --port 8000
```

Run:

```bash
MARKETPULSE_SMOKE_PASSWORD=dev uv run python scripts/smoke_paper_trading_ops.py --base-url http://127.0.0.1:8000
uv run python scripts/check_paper_trading_health.py sqlite:///./marketpulse.db --skip-price-smoke
uv run python scripts/smoke_notifications.py
```

Expected:

- route smoke exits 0;
- health exits 0 or 1 with readable Healthy/Attention output, not traceback;
- notification config smoke exits 0 and sends nothing.

- [ ] **Step 4: Commit verification fixes if needed**

Only if verification required code or doc fixes, inspect the exact changed files
with `git status --short`, stage only those files, and commit:

```bash
git status --short
git commit -m "fix(phase-6h): verification cleanup"
```

---

## Self-Review Checklist

Spec coverage:

- `smoke_paper_trading_ops.py` covers route/auth/read-only smoke.
- `check_paper_trading_health.py` covers DB/audit health and reuses 6f query model semantics.
- `smoke_notifications.py` covers explicit notification smoke with double confirmation.
- Docs cover runbook and release gate checklist.
- No-mutation guards cover static source and runtime row-count checks.

Placeholder scan:

- No placeholder markers or vague "add tests" steps.

Type consistency:

- Script entrypoints use `main(argv: list[str] | None = None) -> int`.
- Health script uses `load_paper_trading_dashboard(session)`.
- Notification script uses `get_notifier_from_settings(settings)` and `send(title, body, url)`.
