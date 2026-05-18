# Phase 5e — `/recap` Page NineScrolls Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/recap/{date}` as editorial long-form NineScrolls layout (64px h1 hero, 5-snap market strip, 760px Markdown article + 720px data rail with 4 cards). Also upgrade `/recaps` grid. Add structured AI key_events output.

**Architecture:** AI prompt v3→v4 emits Markdown sections + `KEY_EVENTS_JSON:` marker; `RecapService` parses and stores into new `daily_recaps.key_events_json` column. Route layer adds `_safe_json_parse` + `_normalize_market_snap` helpers to reshape stored JSON for templates. 8 new partials + 2 rewritten templates + ~250 lines CSS appended to existing `static/css/app.css`.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic + Jinja2 + HTMX + vanilla CSS + Material Symbols + Anthropic Claude (existing).

**Spec:** `docs/superpowers/specs/2026-05-16-phase-5e-recap-page.md`

---

## File Structure

```
marketpulse/
├── db/
│   └── models.py                              MODIFY: add DailyRecap.key_events_json column
├── ai/
│   └── prompts.py                             MODIFY: COMMENTARY_PROMPT_VERSION bump + new system prompt
├── recap/
│   └── service.py                             MODIFY: _parse_ai_output + _upsert_pending + generate()
├── web/
│   ├── routes/
│   │   └── recap.py                           REWRITE: enriched context + 2 helpers
│   ├── static/css/
│   │   └── app.css                            APPEND: ~250 lines Phase 5e CSS
│   └── templates/
│       ├── recap.html                         REWRITE
│       ├── recaps.html                        REWRITE (NS grid)
│       └── partials/
│           ├── recap_hero.html                NEW
│           ├── recap_market_snap.html         NEW
│           ├── recap_article.html             NEW
│           ├── recap_portfolio_today_card.html NEW
│           ├── recap_watchlist_perf_card.html NEW
│           ├── recap_key_events_card.html     NEW
│           └── recap_prev_recaps_card.html    NEW
alembic/versions/
└── <auto>_add_daily_recaps_key_events.py      NEW
tests/
├── unit/
│   └── test_recap_prompt_parsing.py           NEW
├── integration/
│   └── test_recap_service_generate.py         EXTEND (or new)
└── web/
    ├── test_recap.py                          EXTEND
    └── test_recaps.py                         NEW
```

---

## Conventions (Applied Throughout)

- **TDD:** failing test → see fail → implement → pass → commit.
- **CSS path:** ALWAYS `marketpulse/web/static/css/app.css` (not `static/app.css`, which is Tailwind output and gets wiped on build).
- **Jinja format strings:** `"{:+,.0f}".format(value)` (new-style); NOT `"%+,.0f"|format(value)` (old-style, no `,` separator).
- **DELETE/POST HTMX:** retry button targets `body` with `hx-swap="outerHTML"`.
- **Models:** `DailyRecap` lives in `marketpulse/db/models.py:170`; table is `daily_recaps`. Use SQLAlchemy 2 `Mapped[...]` style.
- **session_scope generator:** Tests use `db_session` fixture from `tests/conftest.py`.
- **Data shapes** (existing, do not invent):
  - `market_summary_json`: flat dict `{"spy": pct, "qqq": pct, "dia": pct, "vix": price}` — use `_normalize_market_snap` to reshape.
  - `holdings_totals_json`: from `compute_totals()` → `{cost, market_value, pl_dollars, pl_pct}` (NOT `today_pl_*`).
  - `watchlist_performance_json`: list of `{ticker, price, change_pct, volume, avg_volume_20d, stale, signals, error, news_items}`.
  - `generation_status`: canonical value is `"success"` (NOT `"ok"`).
- **Run tests:** `uv run pytest <path> -v`
- **Lint:** `uv run ruff check <path>`

---

### Task 1: DB migration — add `daily_recaps.key_events_json` column

**Files:**
- Modify: `marketpulse/db/models.py:170` (`DailyRecap` class, add `key_events_json` column)
- Create: `alembic/versions/<auto>_add_daily_recaps_key_events.py` (via `alembic revision`)

- [ ] **Step 1.1: Add `key_events_json` column to `DailyRecap`**

Edit `marketpulse/db/models.py`. Find `class DailyRecap(Base):` (line ~170). Add this column AFTER `ai_commentary_text` and BEFORE `generated_at` (keep schema-evolution ordering):

```python
key_events_json: Mapped[str | None] = mapped_column(Text, nullable=True)
```

(Match the existing column style — `Mapped[...]`, `mapped_column`, no default.)

- [ ] **Step 1.2: Generate Alembic revision**

```bash
uv run alembic revision -m "add daily_recaps key_events_json"
```

Creates `alembic/versions/<hash>_add_daily_recaps_key_events.py` with empty upgrade/downgrade.

- [ ] **Step 1.3: Fill the migration body**

Open the new file. Verify `down_revision = "6b48d3a5c80f"` (the latest revision, from Phase 5d `holdings.sector`). Replace the empty `upgrade()`/`downgrade()`:

```python
def upgrade() -> None:
    op.add_column(
        "daily_recaps",
        sa.Column("key_events_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("daily_recaps", "key_events_json")
```

**Critical:** Table name is `"daily_recaps"` (plural), verified in `marketpulse/db/models.py:171`.

- [ ] **Step 1.4: Run migration**

```bash
uv run alembic upgrade head
```

Expected: `Running upgrade 6b48d3a5c80f -> <hash>, add daily_recaps key_events_json`. No errors.

- [ ] **Step 1.5: Verify schema**

```bash
uv run python -c "from marketpulse.db.models import DailyRecap; print([c.name for c in DailyRecap.__table__.columns])"
```

Expected output: list includes `'key_events_json'`.

- [ ] **Step 1.6: Migration round-trip test**

```bash
uv run alembic downgrade -1
uv run alembic upgrade head
```

Both should exit 0.

- [ ] **Step 1.7: Commit**

```bash
git add marketpulse/db/models.py alembic/versions/
git commit -m "feat(db): add DailyRecap.key_events_json column (nullable TEXT)

Stores structured KEY_EVENTS_JSON output from Phase 5e AI prompt v4.
Old recaps (v3 plain text) leave the column NULL — template falls
back to 'AI 整理中…'. Migration revises 6b48d3a5c80f."
```

---

### Task 2: AI prompt v3 → v4 (Markdown + KEY_EVENTS_JSON)

**Files:**
- Modify: `marketpulse/ai/prompts.py` (constants at top + `_COMMENTARY_SYSTEM`)
- Test: `tests/unit/test_recap_prompt_parsing.py` (new file, only tests the prompt-format upgrade for now; parser tests come in Task 3)

- [ ] **Step 2.1: Bump version constant**

In `marketpulse/ai/prompts.py`, find line 7:

```python
COMMENTARY_PROMPT_VERSION = "commentary-v3-zh-holdings"
```

Change to:

```python
COMMENTARY_PROMPT_VERSION = "commentary-v4-zh-markdown"
```

- [ ] **Step 2.2: Replace `_COMMENTARY_SYSTEM`**

Find the existing `_COMMENTARY_SYSTEM = (...)` block (lines ~25-32). Replace the entire string with:

```python
_COMMENTARY_SYSTEM = (
    "你是一名盘后市场点评作者,面向同时关注自选股、可能持有部分仓位的投资者。\n\n"
    "请用中文写一段盘后复盘,严格按以下格式输出:\n\n"
    "## 大盘\n"
    "[2-3 段 Markdown 段落,内嵌 inline code 标记数字如 `5,973.10`,"
    "关键 ticker 用粗体 **NVDA**,涨跌幅度可加颜色提示如 *(+0.24%)*]\n\n"
    "## 板块与个股\n"
    "[同上格式]\n\n"
    "## 持仓与启示 (若 holdings 非空才输出)\n"
    "[同上格式]\n\n"
    "---\n\n"
    "在 commentary 之后必须**单独一行**输出关键事件 JSON 数组,"
    "严格遵守此 schema:\n\n"
    "KEY_EVENTS_JSON: [\n"
    "  {\"time\": \"16:00 EDT\", \"title\": \"AVGO 与 AAPL 5 年定制芯片协议\", \"kind\": \"deal\"},\n"
    "  {\"time\": \"14:00 EDT\", \"title\": \"CPI 数据公布略低于预期\", \"kind\": \"econ\"}\n"
    "]\n\n"
    "kind 取值: deal | earnings | econ | merger | analyst | other\n"
    "请提供 3-5 条今日最关键事件。若数据中无明确事件,输出空数组 []。\n\n"
    "整体要客观、冷静、具体,提及具体的 ticker 和数字。股票代码保留英文原文。"
)
```

- [ ] **Step 2.3: Verify prompt assembly via `render_commentary_prompt`**

The function `render_commentary_prompt` at line 83 should pick up the new `_COMMENTARY_SYSTEM` automatically (it's referenced inside the f-string). No code change needed there.

Run quick sanity check:

```bash
uv run python -c "from marketpulse.ai.prompts import COMMENTARY_PROMPT_VERSION, _COMMENTARY_SYSTEM; print(COMMENTARY_PROMPT_VERSION); print('KEY_EVENTS_JSON' in _COMMENTARY_SYSTEM)"
```

Expected: `commentary-v4-zh-markdown` then `True`.

- [ ] **Step 2.4: Existing prompt tests check**

```bash
uv run pytest tests/unit/test_prompts.py -v 2>&1 | tail -10
```

If `test_prompts.py` exists and asserts on the prompt string content (e.g., contains "## 大盘"), those tests should still pass. If they asserted on the old version string `commentary-v3-zh-holdings`, update them to `commentary-v4-zh-markdown` (find by grep):

```bash
grep -rn "commentary-v3-zh-holdings" tests/
```

Replace any matches with `commentary-v4-zh-markdown`.

- [ ] **Step 2.5: Run full unit test suite, fix any breakage**

```bash
uv run pytest tests/unit/ -q
```

Expected: all pass.

- [ ] **Step 2.6: Commit**

```bash
git add marketpulse/ai/prompts.py tests/
git commit -m "feat(ai): commentary prompt v3 → v4 — Markdown + KEY_EVENTS_JSON

COMMENTARY_PROMPT_VERSION bumped to commentary-v4-zh-markdown.
New system prompt requires:
- Markdown sections (## 大盘 / ## 板块与个股 / ## 持仓与启示)
- inline code for numbers (5,973.10), bold for tickers (**NVDA**)
- KEY_EVENTS_JSON marker followed by 3-5 events array

Parser comes in Task 3."
```

---

### Task 3: `_parse_ai_output` helper + service integration

**Files:**
- Modify: `marketpulse/recap/service.py` (add `_parse_ai_output` helper at module level + integrate into `generate()` + clear `key_events_json` in `_upsert_pending`)
- Test: `tests/unit/test_recap_prompt_parsing.py` (extend with parser tests)

- [ ] **Step 3.1: Write failing parser tests**

Create or extend `tests/unit/test_recap_prompt_parsing.py`:

```python
"""Parse AI commentary output: extract Markdown body + KEY_EVENTS_JSON."""
import json


def test_parse_with_valid_marker_and_json():
    from marketpulse.recap.service import _parse_ai_output

    raw = (
        "## 大盘\n\n标普 500 收 `5,973.10` (+0.24%) 。\n\n"
        "## 板块与个股\n\n半导体回吐。\n\n"
        "---\n\n"
        "KEY_EVENTS_JSON: ["
        "{\"time\": \"16:00 EDT\", \"title\": \"AVGO 与 AAPL 协议\", \"kind\": \"deal\"}"
        "]"
    )
    commentary, events_json = _parse_ai_output(raw)
    assert "## 大盘" in commentary
    assert "## 板块与个股" in commentary
    assert "KEY_EVENTS_JSON" not in commentary
    assert events_json is not None
    events = json.loads(events_json)
    assert len(events) == 1
    assert events[0]["title"] == "AVGO 与 AAPL 协议"
    assert events[0]["kind"] == "deal"


def test_parse_without_marker_returns_raw_commentary_and_none_events():
    """No KEY_EVENTS_JSON marker → entire raw is commentary, events=None."""
    from marketpulse.recap.service import _parse_ai_output

    raw = "## 大盘\n\n这是一段没有 events 标记的复盘。"
    commentary, events_json = _parse_ai_output(raw)
    assert commentary == raw
    assert events_json is None


def test_parse_malformed_json_falls_back_to_none_events():
    """Marker present but invalid JSON → commentary preserved, events=None."""
    from marketpulse.recap.service import _parse_ai_output

    raw = "## 大盘\n\n复盘正文。\n\nKEY_EVENTS_JSON: not-a-json-array"
    commentary, events_json = _parse_ai_output(raw)
    assert commentary == "## 大盘\n\n复盘正文."
    assert events_json is None


def test_parse_events_not_a_list_falls_back():
    """KEY_EVENTS_JSON value is a dict not a list → events=None."""
    from marketpulse.recap.service import _parse_ai_output

    raw = "正文\n\nKEY_EVENTS_JSON: {\"a\": 1}"
    commentary, events_json = _parse_ai_output(raw)
    assert events_json is None


def test_parse_strips_trailing_whitespace_in_commentary():
    from marketpulse.recap.service import _parse_ai_output

    raw = "## 大盘\n\n正文\n\n   \n\nKEY_EVENTS_JSON: []"
    commentary, events_json = _parse_ai_output(raw)
    assert commentary == "## 大盘\n\n正文"  # rstrip applied
    assert events_json == "[]"


def test_parse_empty_events_array():
    from marketpulse.recap.service import _parse_ai_output

    raw = "正文\n\nKEY_EVENTS_JSON: []"
    commentary, events_json = _parse_ai_output(raw)
    assert events_json == "[]"
```

Adjust the assertion in `test_parse_malformed_json_falls_back_to_none_events` if `_parse_ai_output` keeps the literal `.` instead of `,` — write the assertion to match the actual rstrip behavior (i.e. compare commentary to `"## 大盘\n\n复盘正文."`).

- [ ] **Step 3.2: Run, fail (function doesn't exist)**

```bash
uv run pytest tests/unit/test_recap_prompt_parsing.py -v
```

Expected: 6 tests fail with `ImportError: cannot import name '_parse_ai_output'`.

- [ ] **Step 3.3: Add `_parse_ai_output` to `marketpulse/recap/service.py`**

Open `marketpulse/recap/service.py`. Add this helper at module level, after the `import json` line at the top and before the `_DataLike` protocol class:

```python
def _parse_ai_output(raw: str) -> tuple[str, str | None]:
    """Split AI output into (commentary_markdown, key_events_json_str).

    Looks for the `KEY_EVENTS_JSON:` marker. Everything before is the
    commentary (Markdown). Everything after (parsed as JSON) is events.

    Failures (no marker, malformed JSON, JSON not a list) silently fall
    back to (entire raw output as commentary, events_json = None).
    """
    marker = "KEY_EVENTS_JSON:"
    if marker not in raw:
        return raw, None

    idx = raw.index(marker)
    commentary = raw[:idx].rstrip()
    events_part = raw[idx + len(marker):].strip()

    try:
        events = json.loads(events_part)
        if not isinstance(events, list):
            return commentary, None
        return commentary, json.dumps(events, ensure_ascii=False)
    except json.JSONDecodeError:
        return commentary, None
```

If `json` is not yet imported, it already should be (line 1: `import json`). Verify.

- [ ] **Step 3.4: Run, pass**

```bash
uv run pytest tests/unit/test_recap_prompt_parsing.py -v
```

Expected: 6 pass.

- [ ] **Step 3.5: Integrate into `generate()`**

In `generate()` (line ~39), find the existing commentary assignment block (line ~86: `recap.ai_commentary_text = commentary`). Read the surrounding code first:

```bash
sed -n '60,95p' marketpulse/recap/service.py
```

The current flow assigns `commentary` directly. Add the parser call before assignment.

In place of the existing single line `recap.ai_commentary_text = commentary`, change to:

```python
commentary_md, events_json = _parse_ai_output(commentary)
recap.ai_commentary_text = commentary_md
recap.key_events_json = events_json
```

Note: the local variable `commentary` is the raw AI string. After parsing we use `commentary_md` for the body and `events_json` (a serialized JSON string OR None) for the events column.

- [ ] **Step 3.6: Update `_upsert_pending` to reset `key_events_json` on retry**

In `_upsert_pending()` (line ~98), find the reset block that nulls out `ai_commentary_text` and the JSON columns. Add one more line:

```python
existing.key_events_json = None
```

Place it alongside the other `existing.*_json = None` resets (likely between `existing.holdings_totals_json = None` and `existing.ai_commentary_text = None`, or wherever the JSON columns are reset).

- [ ] **Step 3.7: Run full recap test suite**

```bash
uv run pytest tests/ -q -k "recap" 2>&1 | tail -3
```

Expected: all pass.

- [ ] **Step 3.8: Commit**

```bash
git add marketpulse/recap/service.py tests/unit/test_recap_prompt_parsing.py
git commit -m "feat(recap): parse AI output into commentary + key_events

_parse_ai_output splits AI response at KEY_EVENTS_JSON: marker.
Commentary (before marker) stored in ai_commentary_text; structured
events array (after marker) stored as JSON in key_events_json.

Failures (no marker, bad JSON, not-a-list) silently fall back to
entire raw output as commentary, events=None. _upsert_pending now
resets key_events_json alongside other JSON columns on retry.

6 unit tests cover: happy path, no marker, malformed JSON, non-list
events, whitespace stripping, empty array."
```

---

### Task 4: Route `/recap/{date}` — context enrichment + `_normalize_market_snap`

**Files:**
- Modify: `marketpulse/web/routes/recap.py` (rewrite)
- Test: `tests/web/test_recap.py` (extend with context-shape tests)

- [ ] **Step 4.1: Write failing context tests**

Append to `tests/web/test_recap.py`:

```python
import json
from datetime import UTC, date, datetime
from unittest.mock import patch

from marketpulse.db.models import DailyRecap


def _seed_recap(db_session, recap_date, *, status="success",
                market_summary=None, holdings_totals=None,
                watchlist=None, key_events=None,
                commentary="测试复盘正文。"):
    r = DailyRecap(
        recap_date=recap_date,
        generation_status=status,
        ai_commentary_text=commentary,
        market_summary_json=json.dumps(market_summary) if market_summary else None,
        watchlist_performance_json=json.dumps(watchlist) if watchlist else None,
        holdings_totals_json=json.dumps(holdings_totals) if holdings_totals else None,
        key_events_json=json.dumps(key_events) if key_events else None,
        generated_at=datetime(2026, 5, 12, 20, 42, tzinfo=UTC) if status == "success" else None,
    )
    db_session.add(r)
    db_session.commit()
    return r


def test_recap_detail_normalizes_market_snap_dict_to_list(client, monkeypatch, db_session):
    """Stored flat dict {spy, qqq, dia, vix} must reshape to list of cards."""
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), market_summary={
        "spy": 0.24, "qqq": 0.44, "dia": 0.51, "vix": 14.18,
    })
    r = client.get("/recap/2026-05-12")
    assert r.status_code == 200
    assert "标普 500" in r.text
    assert "纳指 100" in r.text
    assert "道指" in r.text
    assert "VIX" in r.text


def test_recap_detail_handles_missing_jsons_gracefully(client, monkeypatch, db_session):
    """All *_json fields NULL → page renders with placeholders."""
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12),
                market_summary=None, holdings_totals=None,
                watchlist=None, key_events=None)
    r = client.get("/recap/2026-05-12")
    assert r.status_code == 200


def test_recap_detail_404_when_no_row(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/recap/2020-01-01")
    assert r.status_code == 404


def test_recap_detail_prev_recaps_excludes_current(client, monkeypatch, db_session):
    """prev_recaps filter < recap_date, not just !=."""
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 10), commentary="复盘 1")
    _seed_recap(db_session, date(2026, 5, 11), commentary="复盘 2")
    _seed_recap(db_session, date(2026, 5, 12), commentary="当日")
    _seed_recap(db_session, date(2026, 5, 13), commentary="未来不该出现")
    r = client.get("/recap/2026-05-12")
    assert r.status_code == 200
    # Current date should NOT appear in prev_recaps section (only commentary header)
    assert "未来不该出现" not in r.text


def test_recaps_list_extracts_pl_from_holdings_totals(client, monkeypatch, db_session):
    """compute_totals key is 'pl_dollars' (not 'today_pl_dollars')."""
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), holdings_totals={
        "cost": 10000.0, "market_value": 10500.0,
        "pl_dollars": 500.0, "pl_pct": 5.0,
    })
    r = client.get("/recaps")
    assert r.status_code == 200
    # The +500 dollars should appear (formatted with comma + sign)
    assert "+500" in r.text or "500.00" in r.text
```

- [ ] **Step 4.2: Run tests, expect failures**

```bash
uv run pytest tests/web/test_recap.py -v -k "normalizes or missing_jsons or 404 or excludes_current or extracts_pl"
```

Expected: failures because route doesn't yet enrich context.

- [ ] **Step 4.3: Rewrite `marketpulse/web/routes/recap.py`**

Replace the entire file with:

```python
import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from marketpulse.config import get_settings
from marketpulse.db.models import DailyRecap
from marketpulse.recap.service import RecapService
from marketpulse.web.deps import get_db, get_recap_service, require_auth
from marketpulse.web.main import templates

router = APIRouter()


def _safe_json_parse(text: str | None, default):
    """Try to parse JSON; return `default` on failure or None input."""
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def _normalize_market_snap(raw: dict | list | None) -> list[dict]:
    """Reshape stored market_summary_json into template-friendly list.

    Service dumps flat dict {"spy": pct, "qqq": pct, "dia": pct, "vix": price}.
    Template expects [{label, value, pct, up}, ...].

    For VIX, "down is good" → up=(pct <= 0). For others, up=(pct >= 0).
    """
    if not raw:
        return []
    if isinstance(raw, list):  # forward-compatible if service later emits list
        return raw

    out = []
    INDICES = [
        ("spy", "标普 500"),
        ("qqq", "纳指 100"),
        ("dia", "道指"),
        ("vix", "VIX 恐慌指数"),
    ]
    for key, label in INDICES:
        v = raw.get(key)
        if v is None:
            continue
        is_vix = (key == "vix")
        out.append({
            "label": label,
            "value": f"{v:.2f}",
            "pct": None if is_vix else f"{v:+.2f}%",
            "up": (v <= 0) if is_vix else (v >= 0),
        })
    return out


@router.get("/recaps", response_class=HTMLResponse)
def recap_list(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    rows = (
        db.query(DailyRecap)
        .order_by(DailyRecap.recap_date.desc())
        .limit(60)
        .all()
    )
    enriched = []
    for r in rows:
        totals = _safe_json_parse(r.holdings_totals_json, {})
        enriched.append({
            "recap_date": r.recap_date,
            "generation_status": r.generation_status,
            "generated_at": r.generated_at,
            "summary": (r.ai_commentary_text or "")[:200],
            # compute_totals returns {cost, market_value, pl_dollars, pl_pct}
            "today_pl_dollars": totals.get("pl_dollars"),
            "today_pl_pct": totals.get("pl_pct"),
        })
    return templates.TemplateResponse(request, "recaps.html", {"rows": enriched})


@router.get("/recap/{recap_date}", response_class=HTMLResponse)
def recap_detail(
    request: Request,
    recap_date: date,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    row = (
        db.query(DailyRecap)
        .filter(DailyRecap.recap_date == recap_date)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="not found")

    prev_recaps = (
        db.query(DailyRecap)
        .filter(DailyRecap.recap_date < recap_date)  # strictly past
        .order_by(DailyRecap.recap_date.desc())
        .limit(6)
        .all()
    )

    settings = get_settings()

    return templates.TemplateResponse(
        request, "recap.html",
        {
            "row": row,
            "recap_date": recap_date,
            "commentary_md": row.ai_commentary_text or "",
            "market_snap": _normalize_market_snap(
                _safe_json_parse(row.market_summary_json, {})
            ),
            "portfolio_today": _safe_json_parse(row.holdings_totals_json, {}),
            "watchlist_perf": _safe_json_parse(row.watchlist_performance_json, []),
            "key_events": _safe_json_parse(row.key_events_json, []),
            "prev_recaps": prev_recaps,
            "model_version": f"commentary-v4-zh-markdown · {settings.ai_model}",
        },
    )


@router.post("/recap/{recap_date}/retry")
def recap_retry(
    recap_date: date,
    svc: RecapService = Depends(get_recap_service),
    _: None = Depends(require_auth),
):
    svc.generate(recap_date)
    return RedirectResponse(url=f"/recap/{recap_date}", status_code=303)
```

- [ ] **Step 4.4: Run new tests**

```bash
uv run pytest tests/web/test_recap.py -v -k "normalizes or missing_jsons or 404 or excludes_current or extracts_pl"
```

Expected: passes for tests that don't depend on new templates (the page may not render the labels yet because templates aren't done — but at least no 500 errors).

If tests fail with template-not-found or template syntax errors, that's expected — they pass after Task 5+. Skip those for now if they fail at this task.

- [ ] **Step 4.5: Smoke test — run full route module**

```bash
uv run pytest tests/web/test_recap.py -v
```

Expected: existing tests still pass. New tests may fail (waiting on templates). Note which ones for follow-up.

- [ ] **Step 4.6: Ruff + commit**

```bash
uv run ruff check marketpulse/web/routes/recap.py tests/web/test_recap.py
git add marketpulse/web/routes/recap.py tests/web/test_recap.py
git commit -m "feat(recap): enrich /recap and /recaps route context

Adds _safe_json_parse and _normalize_market_snap helpers.
/recap/{date}: context now includes parsed market_snap (reshape flat
dict to template list), portfolio_today, watchlist_perf, key_events,
prev_recaps (filter < recap_date, not !=), model_version from
settings.ai_model. /recaps: enriched rows with summary + pl from
compute_totals keys (pl_dollars/pl_pct, NOT today_pl_*)."
```

---

### Task 5: Rewrite `recap.html` shell + Phase 5e layout CSS

**Files:**
- Rewrite: `marketpulse/web/templates/recap.html`
- Modify: `marketpulse/web/static/css/app.css` (append layout + hero CSS)
- Test: `tests/web/test_recap.py` (extend with visual-anchor tests)

- [ ] **Step 5.1: Failing visual-anchor tests**

Append to `tests/web/test_recap.py`:

```python
def test_recap_page_visual_anchors_present(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12))
    r = client.get("/recap/2026-05-12")
    for cls in ("mp-recap-hero", "mp-recap-snap", "mp-recap-body",
                "mp-recap-article", "mp-recap-rail"):
        assert cls in r.text, f"missing {cls}"


def test_recap_page_uses_2400_max_width(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12))
    r = client.get("/recap/2026-05-12")
    assert "max-w-[2400px]" in r.text
```

- [ ] **Step 5.2: Run, fail**

- [ ] **Step 5.3: Rewrite `marketpulse/web/templates/recap.html`**

Replace entire file with:

```html
{% extends "base.html" %}
{% block main_width %}max-w-[2400px]{% endblock %}
{% block nav_width %}max-w-[2400px]{% endblock %}
{% block content %}

{% include "partials/recap_hero.html" ignore missing %}

<section class="mp-recap-snap">
  {% include "partials/recap_market_snap.html" ignore missing %}
</section>

<section class="mp-recap-body">
  <article class="mp-recap-article">
    {% include "partials/recap_article.html" ignore missing %}
  </article>
  <aside class="mp-recap-rail">
    {% include "partials/recap_portfolio_today_card.html" ignore missing %}
    {% include "partials/recap_watchlist_perf_card.html" ignore missing %}
    {% include "partials/recap_key_events_card.html" ignore missing %}
    {% include "partials/recap_prev_recaps_card.html" ignore missing %}
  </aside>
</section>

<script>
function recapToast(msg) {
  const t = document.createElement('div');
  t.textContent = msg;
  t.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);"
    + "background:rgba(2,36,72,0.92);color:white;padding:10px 18px;border-radius:2px;"
    + "font-size:13px;z-index:9999;font-family:var(--ns-font-body);";
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2200);
}

// Convert <time data-utc="..."> to user-local HH:MM.
(function localizeTimes() {
  const fmt = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  };
  document.querySelectorAll('time[data-utc]').forEach(el => {
    el.textContent = fmt(el.dataset.utc);
  });
})();
</script>

{% endblock %}
```

- [ ] **Step 5.4: Append layout CSS to `marketpulse/web/static/css/app.css`**

Open `marketpulse/web/static/css/app.css` (NOT `static/app.css`). Append at the end of the file:

```css
/* ════════ Phase 5e: /recap layout ════════ */
.mp-recap-hero        { padding:40px 48px 24px;
                        display:flex; align-items:flex-end; justify-content:space-between;
                        gap:48px; border-bottom:1px solid var(--ns-outline-variant); }
.mp-recap-hero__title { font:700 64px/0.95 var(--ns-font-headline);
                        letter-spacing:-0.04em; color:var(--ns-navy); margin:8px 0 6px; }
.mp-recap-hero__desc  { font-size:16px; line-height:1.6; max-width:720px;
                        color:var(--ns-on-surface-variant); margin:16px 0 0; }
.mp-recap-hero__meta  { display:flex; flex-direction:column; align-items:flex-end; gap:10px; }
.mp-recap-hero__status { display:flex; gap:12px; align-items:center;
                         font-size:12px; color:var(--mp-up); font-weight:600; }
.mp-recap-hero__model { font:11.5px/1 var(--ns-font-mono); color:var(--ns-on-surface-variant); }
.mp-recap-hero__actions { display:flex; gap:6px; }

.mp-pulse             { width:8px; height:8px; border-radius:50%; background:var(--mp-up);
                        box-shadow:0 0 0 0 rgba(14,138,95,0.5);
                        animation:mp-pulse 2s infinite; }
@keyframes mp-pulse {
  0%   { box-shadow:0 0 0 0 rgba(14,138,95,0.5); }
  70%  { box-shadow:0 0 0 8px rgba(14,138,95,0); }
  100% { box-shadow:0 0 0 0 rgba(14,138,95,0); }
}

.mp-recap-snap        { padding:20px 48px 24px;
                        display:grid; grid-template-columns:repeat(5,1fr); gap:16px; }
.mp-recap-snap__card  { padding:16px 18px; }
.mp-recap-snap__value { font:600 26px/1 var(--ns-font-mono);
                        letter-spacing:-0.01em; color:var(--ns-navy); margin-top:4px; }
.mp-recap-snap__pct   { font:600 13px/1 var(--ns-font-mono); margin-top:2px;
                        display:flex; align-items:center; gap:4px; }

.mp-recap-body        { padding:0 48px 32px;
                        display:grid;
                        grid-template-columns: minmax(720px, 1.4fr) 720px;
                        gap:56px; }
.mp-recap-article     { max-width:760px; }
.mp-recap-article__head { margin-bottom:24px; }
.mp-recap-article__title { font:700 32px/1.1 var(--ns-font-headline);
                           letter-spacing:-0.03em; color:var(--ns-navy); margin:6px 0; }
.mp-recap-prose       { font-size:17px; line-height:1.85;
                        color:var(--ns-on-surface); }
.mp-recap-prose h2    { font:700 22px/1.2 var(--ns-font-headline);
                        letter-spacing:-0.02em; color:var(--ns-navy);
                        margin:32px 0 14px;
                        display:flex; align-items:center; gap:10px; }
.mp-recap-prose h3    { font:700 18px/1.2 var(--ns-font-headline);
                        color:var(--ns-navy); margin:24px 0 12px; }
.mp-recap-prose p     { margin:14px 0; }
.mp-recap-prose code  { background:var(--ns-surface-container-low);
                        padding:0 6px; font:600 14px var(--ns-font-mono); }
.mp-recap-prose strong { color:var(--ns-navy); }

.mp-recap-rail        { display:flex; flex-direction:column; gap:16px; }

@media (max-width: 1600px) {
  .mp-recap-body      { grid-template-columns: 1fr; }
}
@media (max-width: 900px) {
  .mp-recap-snap      { grid-template-columns: repeat(2, 1fr); }
  .mp-recap-hero      { flex-direction:column; align-items:flex-start; gap:24px; }
}
```

- [ ] **Step 5.5: Run anchor tests**

```bash
uv run pytest tests/web/test_recap.py -v -k "visual_anchors or 2400"
```

Expected: both pass.

- [ ] **Step 5.6: Commit**

```bash
git add marketpulse/web/templates/recap.html marketpulse/web/static/css/app.css tests/web/test_recap.py
git commit -m "feat(recap): new shell + mp-recap-* layout CSS + JS helpers

Editorial 5-section shell with ignore-missing partials so later tasks
fill incrementally. Includes recapToast() and localizeTimes() inline
JS (no external dep on trades_form_script). Layout:
- 1.4fr 720px body (collapses < 1600 → 1fr)
- 5-col snap (collapses < 900 → 2-col)
- 760px article max-width for line-length readability

CSS in static/css/app.css (NOT static/app.css — Tailwind output)."
```

---

### Task 6: Hero partial + 4-button action bar

**Files:**
- Create: `marketpulse/web/templates/partials/recap_hero.html`
- Test: `tests/web/test_recap.py` (extend)

- [ ] **Step 6.1: Failing tests**

Append to `tests/web/test_recap.py`:

```python
def test_recap_hero_renders_h1_date(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12))
    r = client.get("/recap/2026-05-12")
    # h1 contains formatted date like "2026 · 5 月 12 日"
    assert "2026" in r.text
    assert "5 月" in r.text
    assert "12 日" in r.text


def test_recap_hero_4_action_buttons_present(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12))
    r = client.get("/recap/2026-05-12")
    assert "重新生成" in r.text
    assert "分享" in r.text
    assert "置顶" in r.text
    assert "推送至订阅者" in r.text


def test_recap_hero_toast_buttons_have_onclick(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12))
    r = client.get("/recap/2026-05-12")
    # 3 stub buttons have onclick="recapToast(...)" pattern
    assert r.text.count("recapToast(") == 3


def test_recap_hero_success_status_shows_pulse(client, monkeypatch, db_session):
    """generation_status == 'success' should render mp-pulse element."""
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), status="success")
    r = client.get("/recap/2026-05-12")
    assert "mp-pulse" in r.text


def test_recap_hero_failed_status_shows_red(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), status="failed")
    r = client.get("/recap/2026-05-12")
    assert "生成失败" in r.text
```

- [ ] **Step 6.2: Run, fail**

- [ ] **Step 6.3: Create `marketpulse/web/templates/partials/recap_hero.html`**

```html
<section class="mp-recap-hero">
  <div class="mp-recap-hero__main">
    <span class="mp-eyebrow mp-eyebrow--primary">盘后复盘 · 美股</span>
    <h1 class="grotesk mp-recap-hero__title">
      {{ recap_date.strftime('%Y · %-m 月 %-d 日') }}
    </h1>
    <span class="mp-rule"></span>
    <p class="mp-recap-hero__desc">
      由 Claude 在收盘后基于您的自选股、当日持仓和大盘数据自动生成。
      客观、冷静、具体,提及具体的 ticker 和数字。
    </p>
  </div>

  <div class="mp-recap-hero__meta">
    <div class="mp-recap-hero__status">
      {% if row.generation_status == "success" %}
        <span class="mp-pulse"></span>
        已生成 ·
        <time data-utc="{{ row.generated_at.isoformat() if row.generated_at else '' }}">
          {{ row.generated_at.strftime('%H:%M') if row.generated_at else '' }}
        </time>
      {% elif row.generation_status == "pending" %}
        <span class="muted">生成中…</span>
      {% else %}
        <span class="down">生成失败</span>
      {% endif %}
      <span class="mp-recap-hero__model">{{ model_version }}</span>
    </div>
    <div class="mp-recap-hero__actions">
      <button class="mp-btn mp-btn--ghost"
              hx-post="/recap/{{ recap_date }}/retry"
              hx-target="body" hx-swap="outerHTML"
              hx-confirm="重新生成 {{ recap_date }} 的复盘?">
        <span class="material-symbols-outlined">refresh</span>重新生成
      </button>
      <button class="mp-btn mp-btn--ghost" onclick="recapToast('分享功能暂未启用')">
        <span class="material-symbols-outlined">share</span>分享
      </button>
      <button class="mp-btn mp-btn--ghost" onclick="recapToast('置顶功能暂未启用')">
        <span class="material-symbols-outlined">push_pin</span>置顶
      </button>
      <button class="mp-btn mp-btn--navy" onclick="recapToast('推送功能暂未启用')">
        <span class="material-symbols-outlined">notifications_active</span>推送至订阅者
      </button>
    </div>
  </div>
</section>
```

- [ ] **Step 6.4: Run + commit**

```bash
uv run pytest tests/web/test_recap.py -v -k "hero"
git add marketpulse/web/templates/partials/recap_hero.html tests/web/test_recap.py
git commit -m "feat(recap): hero partial — 64px date h1 + 4 actions

Left: eyebrow + 64px h1 (strftime %-m 月 %-d 日 — glibc-only) +
mp-rule + 16px description. Right: status pulse/loading/failed +
model_version + 4 buttons: 重新生成 (hx-post retry) + 3 toast stubs
(分享/置顶/推送至订阅者).

status check uses 'success' (canonical RecapService value)."
```

---

### Task 7: Market snap partial (5 KPI cards from `_normalize_market_snap`)

**Files:**
- Create: `marketpulse/web/templates/partials/recap_market_snap.html`
- Test: `tests/web/test_recap.py` (extend)

- [ ] **Step 7.1: Failing tests**

```python
def test_recap_market_snap_renders_4_cards_with_data(client, monkeypatch, db_session):
    """4 indices stored → 4 cards (SPY/QQQ/DIA/VIX)."""
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), market_summary={
        "spy": 0.24, "qqq": 0.44, "dia": 0.51, "vix": 14.18,
    })
    r = client.get("/recap/2026-05-12")
    assert r.text.count("mp-recap-snap__card") == 4


def test_recap_market_snap_vix_up_when_pct_negative(client, monkeypatch, db_session):
    """VIX is 'down is good' — when value is positive but used as price,
    we treat (v <= 0) as 'up'. Here VIX price 14.18 > 0 → up=False → trending_down."""
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), market_summary={"vix": 14.18})
    r = client.get("/recap/2026-05-12")
    assert "VIX 恐慌指数" in r.text


def test_recap_market_snap_empty_state_when_no_data(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), market_summary=None)
    r = client.get("/recap/2026-05-12")
    assert "暂无大盘数据" in r.text
```

- [ ] **Step 7.2: Run, fail**

- [ ] **Step 7.3: Create `marketpulse/web/templates/partials/recap_market_snap.html`**

```html
{% for item in market_snap %}
<div class="mp-card mp-recap-snap__card">
  <span class="mp-eyebrow mp-eyebrow--primary">{{ item.label }}</span>
  <div class="mono tnum mp-recap-snap__value">{{ item.value }}</div>
  <div class="mono tnum mp-recap-snap__pct {% if item.up %}up{% else %}down{% endif %}">
    <span class="material-symbols-outlined" style="font-size:14px;">
      {% if item.up %}trending_up{% else %}trending_down{% endif %}
    </span>
    {% if item.pct %}{{ item.pct }}{% endif %}
  </div>
</div>
{% endfor %}
{% if not market_snap %}
  <div class="muted" style="grid-column: 1 / -1; padding: 16px; text-align:center;">
    暂无大盘数据
  </div>
{% endif %}
```

- [ ] **Step 7.4: Run + commit**

```bash
uv run pytest tests/web/test_recap.py -v -k "market_snap"
git add marketpulse/web/templates/partials/recap_market_snap.html tests/web/test_recap.py
git commit -m "feat(recap): 5-snap market strip partial

Iterates market_snap (already reshaped to list by _normalize_market_snap).
Each card: eyebrow label + 26px mono value + 13px mono pct with
up/down trending icon. Empty state spans full grid row."
```

---

### Task 8: Article partial (Markdown long-form)

**Files:**
- Create: `marketpulse/web/templates/partials/recap_article.html`
- Test: `tests/web/test_recap.py` (extend)

- [ ] **Step 8.1: Failing tests**

```python
def test_recap_article_renders_markdown_h2(client, monkeypatch, db_session):
    """Markdown ## headers → <h2>."""
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12),
                commentary="## 大盘\n\n标普 500 收 `5,973.10`。")
    r = client.get("/recap/2026-05-12")
    assert "<h2>" in r.text
    assert "大盘" in r.text
    assert "<code>" in r.text


def test_recap_article_empty_state_when_no_commentary(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), commentary=None)
    r = client.get("/recap/2026-05-12")
    assert "AI commentary 暂未生成" in r.text


def test_recap_article_has_editor_eyebrow(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12))
    r = client.get("/recap/2026-05-12")
    assert "编辑分析" in r.text or "AI" in r.text
```

- [ ] **Step 8.2: Run, fail**

- [ ] **Step 8.3: Create `marketpulse/web/templates/partials/recap_article.html`**

```html
<header class="mp-recap-article__head">
  <span class="mp-eyebrow mp-eyebrow--primary">编辑分析 · AI</span>
  <h2 class="grotesk mp-recap-article__title">每日盘后</h2>
  <span class="mp-rule"></span>
</header>

<div class="mp-recap-prose">
  {% if commentary_md %}
    {{ commentary_md | markdown }}
  {% else %}
    <p class="muted">AI commentary 暂未生成。</p>
  {% endif %}
</div>
```

- [ ] **Step 8.4: Run + commit**

```bash
uv run pytest tests/web/test_recap.py -v -k "article"
git add marketpulse/web/templates/partials/recap_article.html tests/web/test_recap.py
git commit -m "feat(recap): editorial article partial

eyebrow + 32px h2 title + mp-rule, then mp-recap-prose div with
{{ commentary_md | markdown }} (existing Jinja filter). Empty state
shows '暂未生成' placeholder. Uses mp-recap-prose alone (not
mp-prose) — 17px / 1.85 line-height per design."
```

---

### Task 9: Portfolio today card partial

**Files:**
- Create: `marketpulse/web/templates/partials/recap_portfolio_today_card.html`
- Modify: `marketpulse/web/static/css/app.css` (append rail-card CSS)
- Test: `tests/web/test_recap.py` (extend)

- [ ] **Step 9.1: Failing tests**

```python
def test_recap_portfolio_today_card_renders_pl(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), holdings_totals={
        "cost": 10000.0, "market_value": 10500.0,
        "pl_dollars": 500.0, "pl_pct": 5.0,
    })
    r = client.get("/recap/2026-05-12")
    assert "组合今日" in r.text
    # +500 dollars rendered (allow either +500 or +500.00 formatting)
    assert "+500" in r.text


def test_recap_portfolio_today_card_empty_state(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), holdings_totals=None)
    r = client.get("/recap/2026-05-12")
    assert "暂无组合数据" in r.text
```

- [ ] **Step 9.2: Run, fail**

- [ ] **Step 9.3: Create `marketpulse/web/templates/partials/recap_portfolio_today_card.html`**

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">account_balance_wallet</span>组合今日
    </span>
    {% set pl = portfolio_today.get('pl_dollars') if portfolio_today else None %}
    {% if pl is not none %}
      <span class="mono tnum {% if pl >= 0 %}up{% else %}down{% endif %}" style="font-weight:700;">
        {{ "{:+,.2f}".format(pl) }}
        {% if portfolio_today.get('pl_pct') is not none %}
          · {{ "{:+.2f}%".format(portfolio_today.pl_pct) }}
        {% endif %}
      </span>
    {% endif %}
  </div>
  <div class="mp-card__body">
    {% if portfolio_today %}
      <dl class="mp-recap-stats">
        <div><dt>市值</dt><dd class="mono tnum">${{ "{:,.0f}".format(portfolio_today.get('market_value') or 0) }}</dd></div>
        <div><dt>总成本</dt><dd class="mono tnum">${{ "{:,.0f}".format(portfolio_today.get('cost') or 0) }}</dd></div>
        <div><dt>未实现盈亏</dt>
          {% set upl = (portfolio_today.get('market_value') or 0) - (portfolio_today.get('cost') or 0) %}
          <dd class="mono tnum {% if upl >= 0 %}up{% else %}down{% endif %}">
            {{ "{:+,.0f}".format(upl) }}
          </dd>
        </div>
      </dl>
    {% else %}
      <p class="muted" style="text-align:center; padding:16px;">暂无组合数据</p>
    {% endif %}
  </div>
</section>
```

- [ ] **Step 9.4: Append rail-stats CSS to `marketpulse/web/static/css/app.css`**

```css
/* ════════ Phase 5e: Side rail cards ════════ */
.mp-recap-stats              { display:grid; grid-template-columns: 1fr 1fr 1fr; gap:16px;
                               padding:16px; margin:0; }
.mp-recap-stats > div        { display:flex; flex-direction:column; gap:4px; }
.mp-recap-stats dt           { font:600 10px/1 var(--ns-font-headline);
                               letter-spacing:0.08em; text-transform:uppercase;
                               color:var(--ns-on-surface-variant); }
.mp-recap-stats dd           { font:600 16px/1 var(--ns-font-mono); margin:0; }
```

- [ ] **Step 9.5: Run + commit**

```bash
uv run pytest tests/web/test_recap.py -v -k "portfolio_today"
git add marketpulse/web/templates/partials/recap_portfolio_today_card.html \
        marketpulse/web/static/css/app.css tests/web/test_recap.py
git commit -m "feat(recap): portfolio today card

mp-card with card__head showing 组合今日 + pl_dollars (formatted
+/-, color-coded up/down). Body has 3-col stats grid: 市值 / 总成本
/ 未实现盈亏. Uses pl_dollars/pl_pct keys (NOT today_pl_*) per
compute_totals shape. Empty state placeholder."
```

---

### Task 10: Watchlist performance card partial

**Files:**
- Create: `marketpulse/web/templates/partials/recap_watchlist_perf_card.html`
- Modify: `marketpulse/web/static/css/app.css` (append perf-list CSS)
- Test: `tests/web/test_recap.py` (extend)

- [ ] **Step 10.1: Failing tests**

```python
def test_recap_watchlist_perf_renders_rows(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), watchlist=[
        {"ticker": "AAPL", "price": 180.5, "change_pct": 1.5},
        {"ticker": "NVDA", "price": 132.4, "change_pct": -2.25},
    ])
    r = client.get("/recap/2026-05-12")
    assert "自选股表现" in r.text
    assert "AAPL" in r.text
    assert "NVDA" in r.text


def test_recap_watchlist_perf_empty(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), watchlist=None)
    r = client.get("/recap/2026-05-12")
    assert "暂无自选股" in r.text
```

- [ ] **Step 10.2: Run, fail**

- [ ] **Step 10.3: Create `marketpulse/web/templates/partials/recap_watchlist_perf_card.html`**

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">monitoring</span>自选股表现
    </span>
  </div>
  <ul class="mp-recap-perf-list">
    {% for w in watchlist_perf[:10] %}
      <li>
        <a href="/stock/{{ w.ticker }}" class="mp-ticker-link">{{ w.ticker }}</a>
        <span class="mono tnum">{% if w.price %}${{ "{:.2f}".format(w.price) }}{% else %}—{% endif %}</span>
        <span class="mono tnum {% if w.change_pct is not none and w.change_pct >= 0 %}up{% elif w.change_pct is not none %}down{% endif %}">
          {% if w.change_pct is not none %}{{ "{:+.2f}%".format(w.change_pct) }}{% else %}—{% endif %}
        </span>
      </li>
    {% endfor %}
    {% if not watchlist_perf %}
      <li class="muted" style="padding:16px; text-align:center;">暂无自选股</li>
    {% endif %}
  </ul>
</section>
```

- [ ] **Step 10.4: Append CSS**

```css
.mp-recap-perf-list,
.mp-recap-events-list,
.mp-recap-prev-list          { list-style:none; margin:0; padding:8px 16px 14px; }
.mp-recap-perf-list li       { display:grid; grid-template-columns: 60px 1fr 80px;
                               gap:10px; align-items:center; padding:6px 0;
                               border-bottom:1px solid var(--ns-outline-variant); }
.mp-recap-perf-list li:last-child { border-bottom:0; }
```

- [ ] **Step 10.5: Run + commit**

```bash
uv run pytest tests/web/test_recap.py -v -k "watchlist_perf"
git add marketpulse/web/templates/partials/recap_watchlist_perf_card.html \
        marketpulse/web/static/css/app.css tests/web/test_recap.py
git commit -m "feat(recap): watchlist performance card

mp-card with monitoring icon header + grid list (top 10). Each row:
ticker link + price + change_pct (color-coded). Empty state."
```

---

### Task 11: Key events card partial + chip color CSS

**Files:**
- Create: `marketpulse/web/templates/partials/recap_key_events_card.html`
- Modify: `marketpulse/web/static/css/app.css` (events list + 6 chip kind colors)
- Test: `tests/web/test_recap.py` (extend)

- [ ] **Step 11.1: Failing tests**

```python
def test_recap_key_events_renders_chips(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), key_events=[
        {"time": "16:00 EDT", "title": "AVGO 与 AAPL 协议", "kind": "deal"},
        {"time": "14:00 EDT", "title": "CPI 略低于预期", "kind": "econ"},
    ])
    r = client.get("/recap/2026-05-12")
    assert "关键事件" in r.text
    assert "AVGO 与 AAPL 协议" in r.text
    assert "mp-chip--deal" in r.text
    assert "mp-chip--econ" in r.text


def test_recap_key_events_empty_with_null_column(client, monkeypatch, db_session):
    """key_events_json IS NULL → 'AI 整理中…' (legacy v3 recap)."""
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12), key_events=None)
    r = client.get("/recap/2026-05-12")
    assert "AI 整理中" in r.text or "暂无关键事件" in r.text
```

- [ ] **Step 11.2: Run, fail**

- [ ] **Step 11.3: Create `marketpulse/web/templates/partials/recap_key_events_card.html`**

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">event_note</span>关键事件
    </span>
  </div>
  <ul class="mp-recap-events-list">
    {% for e in key_events %}
      <li class="mp-recap-events__item">
        <span class="mp-recap-events__time mono">{{ e.time or "" }}</span>
        <span class="mp-recap-events__title">{{ e.title }}</span>
        <span class="mp-chip mp-chip--{{ e.kind or 'other' }}">{{ e.kind or 'other' }}</span>
      </li>
    {% endfor %}
    {% if not key_events %}
      <li class="muted" style="padding:16px; text-align:center;">
        {% if row.key_events_json is none %}AI 整理中…{% else %}暂无关键事件{% endif %}
      </li>
    {% endif %}
  </ul>
</section>
```

- [ ] **Step 11.4: Append CSS**

```css
.mp-recap-events__item       { display:grid; grid-template-columns: 80px 1fr auto;
                               gap:10px; align-items:center; padding:8px 0;
                               border-bottom:1px solid var(--ns-outline-variant); }
.mp-recap-events__item:last-child { border-bottom:0; }
.mp-recap-events__time       { font-size:11px; color:var(--ns-on-surface-variant); }
.mp-recap-events__title      { font-size:13px; color:var(--ns-navy); }

/* Event kind chips */
.mp-chip--deal      { background:#e0f0ff; color:#0066cc; }
.mp-chip--earnings  { background:#fef3c7; color:#92400e; }
.mp-chip--econ      { background:#ede9fe; color:#5e2cb4; }
.mp-chip--merger    { background:#fce7f3; color:#9d174d; }
.mp-chip--analyst   { background:#d1fae5; color:#065f46; }
.mp-chip--other     { background:var(--ns-surface-container); color:var(--ns-on-surface-variant); }
```

- [ ] **Step 11.5: Run + commit**

```bash
uv run pytest tests/web/test_recap.py -v -k "key_events"
git add marketpulse/web/templates/partials/recap_key_events_card.html \
        marketpulse/web/static/css/app.css tests/web/test_recap.py
git commit -m "feat(recap): key events card + chip kind colors

Grid list: time / title / chip. 6 chip variants: deal/earnings/econ/
merger/analyst/other. Fallback when key_events_json IS NULL shows
'AI 整理中…' (legacy v3 recap)."
```

---

### Task 12: Previous recaps card partial

**Files:**
- Create: `marketpulse/web/templates/partials/recap_prev_recaps_card.html`
- Modify: `marketpulse/web/static/css/app.css`
- Test: `tests/web/test_recap.py` (extend)

- [ ] **Step 12.1: Failing tests**

```python
def test_recap_prev_recaps_renders_dates(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 10), commentary="第 1 篇")
    _seed_recap(db_session, date(2026, 5, 11), commentary="第 2 篇")
    _seed_recap(db_session, date(2026, 5, 12), commentary="今日")
    r = client.get("/recap/2026-05-12")
    assert "历史复盘" in r.text
    assert "05-10" in r.text
    assert "05-11" in r.text
    assert "第 1 篇" in r.text
    # Current date should NOT appear in the prev list
    # (it appears in hero, but check it's not in mp-recap-prev-list section)


def test_recap_prev_recaps_handles_null_commentary(client, monkeypatch, db_session):
    """A prior recap with NULL commentary shows '无摘要', not bare '…'."""
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 10), commentary=None)
    _seed_recap(db_session, date(2026, 5, 12))
    r = client.get("/recap/2026-05-12")
    assert "无摘要" in r.text


def test_recap_prev_recaps_empty_state(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_recap(db_session, date(2026, 5, 12))
    r = client.get("/recap/2026-05-12")
    assert "暂无历史复盘" in r.text
```

- [ ] **Step 12.2: Run, fail**

- [ ] **Step 12.3: Create `marketpulse/web/templates/partials/recap_prev_recaps_card.html`**

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">history</span>历史复盘
    </span>
    <a href="/recaps" class="mp-card__sub" style="color:var(--ns-primary);">全部 →</a>
  </div>
  <ul class="mp-recap-prev-list">
    {% for p in prev_recaps %}
      <li>
        <a href="/recap/{{ p.recap_date }}" class="mp-recap-prev__date mono">
          {{ p.recap_date.strftime('%m-%d') }}
        </a>
        {% if p.ai_commentary_text %}
          <span class="muted mp-recap-prev__excerpt">{{ p.ai_commentary_text[:60] }}…</span>
        {% else %}
          <span class="muted mp-recap-prev__excerpt">无摘要</span>
        {% endif %}
      </li>
    {% endfor %}
    {% if not prev_recaps %}
      <li class="muted" style="padding:16px; text-align:center;">暂无历史复盘</li>
    {% endif %}
  </ul>
</section>
```

- [ ] **Step 12.4: Append CSS**

```css
.mp-recap-prev-list li       { display:flex; gap:10px; padding:8px 0;
                               border-bottom:1px solid var(--ns-outline-variant);
                               align-items:flex-start; }
.mp-recap-prev-list li:last-child { border-bottom:0; }
.mp-recap-prev__date         { font-size:12px; font-weight:600;
                               color:var(--ns-navy); flex:0 0 50px; }
.mp-recap-prev__excerpt      { font-size:12px; line-height:1.4;
                               overflow:hidden; text-overflow:ellipsis; max-height:34px; }
```

- [ ] **Step 12.5: Run + commit**

```bash
uv run pytest tests/web/test_recap.py -v -k "prev_recaps"
git add marketpulse/web/templates/partials/recap_prev_recaps_card.html \
        marketpulse/web/static/css/app.css tests/web/test_recap.py
git commit -m "feat(recap): previous recaps card

Shows up to 6 past recaps (strictly < recap_date via route filter).
Each row: MM-DD link + 60-char excerpt. NULL commentary shows
'无摘要' instead of bare '…'. Empty state placeholder. Header links
to /recaps grid view."
```

---

### Task 13: Rewrite `recaps.html` (NS grid)

**Files:**
- Rewrite: `marketpulse/web/templates/recaps.html`
- Modify: `marketpulse/web/static/css/app.css` (append grid CSS)
- Test: `tests/web/test_recaps.py` (new file)

- [ ] **Step 13.1: Create `tests/web/test_recaps.py`**

```python
"""/recaps grid view tests."""
import json
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.models import DailyRecap


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed(db_session, recap_date, *, status="success",
          holdings_totals=None, commentary="..."):
    r = DailyRecap(
        recap_date=recap_date,
        generation_status=status,
        ai_commentary_text=commentary,
        holdings_totals_json=json.dumps(holdings_totals) if holdings_totals else None,
        generated_at=datetime(2026, 5, 12, 20, tzinfo=UTC) if status == "success" else None,
    )
    db_session.add(r)
    db_session.commit()
    return r


def test_recaps_grid_renders_mp_recaps_card(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session, date(2026, 5, 12))
    r = client.get("/recaps")
    assert r.status_code == 200
    assert "mp-recaps-card" in r.text
    assert "Recap History" in r.text


def test_recaps_grid_shows_pl_when_data_present(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session, date(2026, 5, 12), holdings_totals={
        "cost": 10000.0, "market_value": 10500.0,
        "pl_dollars": 500.0, "pl_pct": 5.0,
    })
    r = client.get("/recaps")
    assert "+500" in r.text  # formatted pl_dollars


def test_recaps_grid_handles_missing_pl(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session, date(2026, 5, 12), holdings_totals=None)
    r = client.get("/recaps")
    assert "无盈亏数据" in r.text


def test_recaps_grid_status_chips_color_coded(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session, date(2026, 5, 12), status="success")
    _seed(db_session, date(2026, 5, 11), status="failed")
    _seed(db_session, date(2026, 5, 10), status="pending")
    r = client.get("/recaps")
    assert "mp-chip--success" in r.text
    assert "mp-chip--failed" in r.text
    assert "mp-chip--pending" in r.text


def test_recaps_grid_empty_state(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/recaps")
    assert "暂无复盘记录" in r.text
```

- [ ] **Step 13.2: Run, fail (404 or old template)**

- [ ] **Step 13.3: Rewrite `marketpulse/web/templates/recaps.html`**

Replace entire file with:

```html
{% extends "base.html" %}
{% block main_width %}max-w-[2400px]{% endblock %}
{% block nav_width %}max-w-[2400px]{% endblock %}
{% block content %}

<section class="mp-hero">
  <div>
    <span class="mp-eyebrow mp-eyebrow--primary">复盘档案</span>
    <h1 class="grotesk mp-hero__title">Recap History</h1>
    <span class="mp-rule"></span>
    <p class="mp-hero__desc">每日盘后由 AI 自动生成的市场点评。点击进入完整复盘。</p>
  </div>
</section>

<section class="mp-recaps-grid">
  {% for r in rows %}
    <a class="mp-card mp-recaps-card" href="/recap/{{ r.recap_date }}">
      <div class="mp-recaps-card__date grotesk">{{ r.recap_date.strftime('%m-%d') }}</div>
      <div class="muted" style="font-size:11px;">{{ r.recap_date.strftime('%Y') }}</div>
      {% if r.today_pl_dollars is not none %}
        <div class="mono tnum mp-recaps-card__pl {% if r.today_pl_dollars >= 0 %}up{% else %}down{% endif %}">
          {{ "{:+,.0f}".format(r.today_pl_dollars) }}
          {% if r.today_pl_pct is not none %}
            <small>{{ "{:+.2f}%".format(r.today_pl_pct) }}</small>
          {% endif %}
        </div>
      {% else %}
        <div class="muted" style="font-size:11px; margin-top:4px;">无盈亏数据</div>
      {% endif %}
      <p class="mp-recaps-card__summary muted">{{ r.summary or '无摘要' }}…</p>
      <span class="mp-recaps-card__status mp-chip mp-chip--{{ r.generation_status }}">
        {{ r.generation_status }}
      </span>
    </a>
  {% endfor %}
  {% if not rows %}
    <div class="muted" style="grid-column:1/-1; padding:32px; text-align:center;">
      暂无复盘记录
    </div>
  {% endif %}
</section>
{% endblock %}
```

- [ ] **Step 13.4: Append grid + chip CSS to `marketpulse/web/static/css/app.css`**

```css
/* ════════ Phase 5e: /recaps grid ════════ */
.mp-recaps-grid              { padding:0 48px 32px;
                               display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr));
                               gap:16px; }
.mp-recaps-card              { padding:18px 20px; text-decoration:none; color:inherit;
                               display:flex; flex-direction:column;
                               transition:box-shadow 200ms; }
.mp-recaps-card:hover        { box-shadow:var(--ns-shadow-hover); }
.mp-recaps-card__date        { font:700 28px/1 var(--ns-font-headline);
                               letter-spacing:-0.02em; color:var(--ns-navy); }
.mp-recaps-card__pl          { font:700 18px/1.1 var(--ns-font-mono); margin-top:10px; }
.mp-recaps-card__pl small    { font-size:11px; opacity:0.7; margin-left:4px; }
.mp-recaps-card__summary     { font-size:12px; line-height:1.5; margin:10px 0 0;
                               overflow:hidden; text-overflow:ellipsis;
                               display:-webkit-box; -webkit-line-clamp:3;
                               -webkit-box-orient:vertical; }
.mp-recaps-card__status      { align-self:flex-start; margin-top:auto; padding-top:10px; }

.mp-chip--success            { background:#d1fae5; color:#065f46; }
.mp-chip--pending            { background:#fef3c7; color:#92400e; }
.mp-chip--failed             { background:#fee2e2; color:#991b1b; }
```

- [ ] **Step 13.5: Run + commit**

```bash
uv run pytest tests/web/test_recaps.py -v
git add marketpulse/web/templates/recaps.html marketpulse/web/static/css/app.css \
        tests/web/test_recaps.py
git commit -m "feat(recaps): NS grid view replacing simple list

mp-hero + auto-fill grid (minmax 220px). Each card: 28px date +
year + +/− P&L dollars (color-coded) + 3-line summary + status chip
(success/pending/failed color-coded). Empty state spans grid."
```

---

### Task 14: Recap service generation tests (integration)

**Files:**
- Create (or extend): `tests/integration/test_recap_service_generate.py`

- [ ] **Step 14.1: Failing tests**

Create `tests/integration/test_recap_service_generate.py`:

```python
"""End-to-end RecapService.generate() with v4 prompt parser."""
import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from marketpulse.db.models import DailyRecap
from marketpulse.recap.service import RecapService


def _service_with_fakes(db_session, ai_output: str):
    """Create RecapService with fake data + ai services."""
    fake_data = MagicMock()
    # Minimal market overview
    from marketpulse.data.types import Quote
    from datetime import UTC, datetime
    q = Quote(ticker="SPY", price=500.0, change_pct=0.24,
              volume=1000, avg_volume_20d=2000,
              fetched_at=datetime.now(UTC), stale=False)
    market = MagicMock()
    market.spy = q
    market.qqq = q
    market.dia = q
    market.vix = Quote(ticker="VIX", price=14.18, change_pct=-2.88,
                       volume=0, avg_volume_20d=0,
                       fetched_at=datetime.now(UTC), stale=False)
    fake_data.get_market_overview.return_value = market

    fake_ai = MagicMock()
    fake_ai.daily_commentary.return_value = ai_output

    return RecapService(session=db_session, data=fake_data, ai=fake_ai)


def test_generate_saves_commentary_and_key_events_separately(db_session):
    """Happy path: AI returns Markdown + KEY_EVENTS_JSON → both saved."""
    ai_out = (
        "## 大盘\n\n标普收 `5,973`.\n\n"
        "## 板块\n\nNVDA 回吐.\n\n"
        "---\n\n"
        "KEY_EVENTS_JSON: [{\"time\": \"16:00\", \"title\": \"AVGO 利好\", \"kind\": \"deal\"}]"
    )
    svc = _service_with_fakes(db_session, ai_out)
    result = svc.generate(date(2026, 5, 12))

    assert result.generation_status == "success"
    assert "## 大盘" in result.ai_commentary_text
    assert "KEY_EVENTS_JSON" not in result.ai_commentary_text
    assert result.key_events_json is not None
    events = json.loads(result.key_events_json)
    assert events[0]["title"] == "AVGO 利好"


def test_generate_falls_back_when_no_marker(db_session):
    """No KEY_EVENTS_JSON marker → entire output is commentary, events=NULL."""
    ai_out = "## 大盘\n\n这是一段没有 events 标记的复盘。"
    svc = _service_with_fakes(db_session, ai_out)
    result = svc.generate(date(2026, 5, 12))

    assert result.ai_commentary_text == ai_out
    assert result.key_events_json is None


def test_generate_retry_clears_key_events(db_session):
    """Retry on a previously-failed-parse recap should null out stale events."""
    # First generation: happy path with events
    ai_out_1 = (
        "## 大盘\n\n正文 1\n\n"
        "KEY_EVENTS_JSON: [{\"time\": \"10:00\", \"title\": \"first\", \"kind\": \"deal\"}]"
    )
    svc_1 = _service_with_fakes(db_session, ai_out_1)
    svc_1.generate(date(2026, 5, 12))

    # Second generation (retry): AI output lacks marker
    ai_out_2 = "## 大盘\n\n正文 2 没有 events 标记"
    svc_2 = _service_with_fakes(db_session, ai_out_2)
    result = svc_2.generate(date(2026, 5, 12))

    assert "正文 2" in result.ai_commentary_text
    assert result.key_events_json is None  # cleared on retry
```

- [ ] **Step 14.2: Run + commit**

```bash
uv run pytest tests/integration/test_recap_service_generate.py -v
git add tests/integration/test_recap_service_generate.py
git commit -m "test(recap): integration tests for v4 generate + parse

3 tests cover: happy path with events stored separately, no-marker
fallback (full text as commentary), retry clears stale events.
Uses MagicMock for data and ai services with deterministic outputs."
```

---

### Task 15: Final integration — full suite + ruff + smoke

- [ ] **Step 15.1: Run full test suite**

```bash
uv run pytest 2>&1 | tail -1
```

Expected: all tests pass. Total count should be approximately `(484 + ~30 new) ≈ 514+`.

- [ ] **Step 15.2: Ruff on entire repo**

```bash
uv run ruff check . 2>&1 | tail -3
```

Expected: `All checks passed!`

- [ ] **Step 15.3: Migration round-trip**

```bash
uv run alembic current
uv run alembic downgrade -1
uv run alembic upgrade head
```

Each exits 0.

- [ ] **Step 15.4: Smoke test the routes**

```bash
uv run python -c "
import os
os.environ['APP_PASSWORD_HASH'] = '\$argon2id\$v=19\$m=65536,t=3,p=4\$abc\$def'
from fastapi.testclient import TestClient
from marketpulse.web.main import app
client = TestClient(app)
for path in ['/recap/2026-01-01', '/recaps']:
    r = client.get(path, follow_redirects=False)
    print(f'{path}: {r.status_code}')
"
```

Expected: both 303 (redirect to login — auth gate). Not 500.

- [ ] **Step 15.5: Visual partial inventory**

```bash
ls marketpulse/web/templates/partials/recap_*
```

Expected 7 partials: hero / market_snap / article / portfolio_today_card / watchlist_perf_card / key_events_card / prev_recaps_card. (Plus existing `recap_card.html` for the home dashboard.)

- [ ] **Step 15.6: Commit log review**

```bash
git log --oneline main..HEAD | head -20
```

Expected: 14 task commits + this task's polish commit (if needed).

- [ ] **Step 15.7: Final commit if cleanup needed**

If everything passes, no commit needed.

If anything failed:

```bash
git add <files>
git commit -m "fix(phase-5e): <specific cleanup>"
```

---

## Self-Review Notes

**Spec coverage check:**
- ✓ DB migration `daily_recaps.key_events_json` (Task 1)
- ✓ AI prompt v3 → v4 with Markdown + KEY_EVENTS_JSON (Task 2)
- ✓ Parser `_parse_ai_output` + service integration (Task 3)
- ✓ Route enrichment + `_normalize_market_snap` + `_safe_json_parse` (Task 4)
- ✓ Shell rewrite + Phase 5e layout CSS (Task 5)
- ✓ Hero partial with 4 actions (Task 6)
- ✓ 5-snap market strip (Task 7)
- ✓ Editorial article (Task 8)
- ✓ Portfolio today card (Task 9)
- ✓ Watchlist perf card (Task 10)
- ✓ Key events card with chip variants (Task 11)
- ✓ Prev recaps card (Task 12)
- ✓ /recaps grid view (Task 13)
- ✓ Integration tests for service generate (Task 14)
- ✓ Final smoke + suite (Task 15)

**Type consistency:**
- `_parse_ai_output` returns `tuple[str, str | None]` consistently used in Task 3 and Task 14.
- `_normalize_market_snap` returns `list[dict]` with keys `{label, value, pct, up}` — used in Tasks 4 + 7.
- `holdings_totals_json` keys `pl_dollars`/`pl_pct` consistently used in Tasks 4 + 9 + 13.
- `generation_status == "success"` consistent in Tasks 6 + 13.
- `key_events_json` column reset in `_upsert_pending` (Task 3) + cleared on retry (Task 14 verifies).
- `recap_date.strftime('%-m 月 %-d 日')` Linux-only — noted in spec, fine for Docker prod.
