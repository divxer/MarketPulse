# MarketPulse v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted FastAPI + HTMX + SQLite app that manages a US-stock watchlist, generates an automated daily recap after market close, and provides on-demand AI deep analysis via Claude.

**Architecture:** Single Python process — FastAPI web server with in-process APScheduler, SQLite via SQLAlchemy 2.x, Jinja2 + HTMX UI, deployed as one Docker image to Fly.io with a persistent volume.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, APScheduler, yfinance, Anthropic SDK (`claude-sonnet-4-6`), Jinja2, HTMX, Tailwind, uv, pytest, structlog, Fly.io.

**Spec:** [docs/superpowers/specs/2026-05-10-marketpulse-design.md](../specs/2026-05-10-marketpulse-design.md)

---

## File Map

```
MarketPulse/
├─ pyproject.toml                        # uv, deps, ruff/pytest config
├─ uv.lock
├─ .python-version                       # 3.12
├─ .gitignore
├─ .env.example
├─ Dockerfile
├─ fly.toml
├─ alembic.ini
├─ alembic/env.py
├─ alembic/versions/0001_initial.py
├─ marketpulse/
│   ├─ __init__.py
│   ├─ config.py                         # pydantic-settings
│   ├─ logging.py                        # structlog setup
│   ├─ db/
│   │   ├─ __init__.py
│   │   ├─ base.py                       # engine, SessionLocal, Base
│   │   └─ models.py                     # all ORM models
│   ├─ data/
│   │   ├─ __init__.py                   # public surface
│   │   ├─ types.py                      # dataclasses for Quote/Bar/News/etc.
│   │   ├─ yfinance_client.py            # thin yfinance wrapper (replaceable)
│   │   ├─ cache.py                      # price_cache & news_cache repositories
│   │   └─ service.py                    # get_quote/get_history/get_news/...
│   ├─ ai/
│   │   ├─ __init__.py
│   │   ├─ types.py                      # AnalysisResult dataclass
│   │   ├─ prompts.py                    # templates + PROMPT_VERSION constants
│   │   ├─ client.py                     # Anthropic SDK wrapper (mockable)
│   │   └─ service.py                    # analyze() + daily_commentary()
│   ├─ recap/
│   │   ├─ __init__.py
│   │   ├─ signals.py                    # pure signal-detection functions
│   │   └─ service.py                    # generate_daily_recap orchestration
│   ├─ scheduler/
│   │   ├─ __init__.py
│   │   └─ jobs.py                       # APScheduler config + jobs
│   ├─ auth/
│   │   ├─ __init__.py
│   │   ├─ password.py                   # bcrypt hash/verify
│   │   └─ session.py                    # signed-cookie helpers
│   └─ web/
│       ├─ __init__.py
│       ├─ main.py                       # FastAPI app factory
│       ├─ deps.py                       # FastAPI dependencies (db, current_user)
│       ├─ routes/
│       │   ├─ __init__.py
│       │   ├─ auth.py
│       │   ├─ home.py
│       │   ├─ watchlist.py
│       │   ├─ stock.py
│       │   ├─ recap.py
│       │   └─ health.py
│       ├─ templates/
│       │   ├─ base.html
│       │   ├─ login.html
│       │   ├─ home.html
│       │   ├─ watchlist.html
│       │   ├─ stock.html
│       │   ├─ recap.html
│       │   ├─ recaps.html
│       │   └─ partials/
│       │       ├─ watchlist_row.html
│       │       ├─ recap_card.html
│       │       └─ analysis_block.html
│       └─ static/
│           └─ app.css                   # Tailwind output (built)
├─ tests/
│   ├─ __init__.py
│   ├─ conftest.py
│   ├─ unit/
│   │   ├─ test_signals.py
│   │   ├─ test_price_cache.py
│   │   ├─ test_news_cache.py
│   │   ├─ test_prompts.py
│   │   └─ test_password.py
│   ├─ integration/
│   │   ├─ test_data_service.py
│   │   ├─ test_ai_service.py
│   │   └─ test_recap_service.py
│   └─ web/
│       ├─ test_auth.py
│       ├─ test_health.py
│       ├─ test_watchlist.py
│       ├─ test_stock.py
│       └─ test_recap.py
└─ scripts/
    └─ smoke_test.py
```

**Boundary rules:**
- `data/yfinance_client.py` is the only file allowed to import `yfinance`.
- `ai/client.py` is the only file allowed to import `anthropic`.
- `web/` consumes services through `data.service`, `ai.service`, `recap.service` — never reaches into `yfinance_client` or `client` directly.

---

## Phase 0 — Project Skeleton

### Task 1: Initialize project with uv

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `marketpulse/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Verify uv is installed**

Run: `uv --version`
Expected: prints a version (e.g. `uv 0.5.x`). If missing, install per https://docs.astral.sh/uv/.

- [ ] **Step 2: Create `.python-version`**

```
3.12
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[project]
name = "marketpulse"
version = "0.1.0"
description = "Self-hosted US stock watchlist + daily recap + AI analysis"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy>=2.0",
    "alembic>=1.14",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "jinja2>=3.1",
    "itsdangerous>=2.2",
    "bcrypt>=4.2",
    "apscheduler>=3.10",
    "yfinance>=0.2.50",
    "anthropic>=0.40",
    "httpx>=0.27",
    "structlog>=24.4",
    "python-multipart>=0.0.20",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "respx>=0.21",
    "ruff>=0.7",
    "mypy>=1.13",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-ra -q"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]
```

- [ ] **Step 4: Create `.gitignore`**

```
__pycache__/
*.pyc
.venv/
.env
*.db
*.db-journal
.pytest_cache/
.ruff_cache/
.mypy_cache/
node_modules/
marketpulse/web/static/app.css
dist/
build/
*.egg-info/
```

- [ ] **Step 5: Create `.env.example`**

```
APP_PASSWORD_HASH=
SESSION_SECRET=change-me-32-bytes-of-randomness-please
ANTHROPIC_API_KEY=
DATABASE_URL=sqlite:///./marketpulse.db
WATCHLIST_RECAP_TIME=16:30
LOG_LEVEL=INFO
AI_MODEL=claude-sonnet-4-6
AI_CACHE_TTL_HOURS=24
```

- [ ] **Step 6: Create empty package init files**

`marketpulse/__init__.py`:
```python
__version__ = "0.1.0"
```

`tests/__init__.py`: empty file.

- [ ] **Step 7: Lock and install**

Run: `uv sync`
Expected: creates `.venv/` and `uv.lock`, installs all deps.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore .env.example marketpulse/__init__.py tests/__init__.py
git commit -m "chore: initialize uv project with deps"
```

---

### Task 2: Configuration module

**Files:**
- Create: `marketpulse/config.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_config.py`:
```python
import os

import pytest

from marketpulse.config import Settings


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "z")
    s = Settings()
    assert s.database_url == "sqlite:///./marketpulse.db"
    assert s.watchlist_recap_time == "16:30"
    assert s.ai_model == "claude-sonnet-4-6"
    assert s.ai_cache_ttl_hours == 24
    assert s.log_level == "INFO"


def test_settings_requires_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("APP_PASSWORD_HASH", "SESSION_SECRET", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(Exception):
        Settings()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: FAIL — `marketpulse.config` not found.

- [ ] **Step 3: Implement `marketpulse/config.py`**

```python
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_password_hash: str = Field(..., alias="APP_PASSWORD_HASH")
    session_secret: str = Field(..., alias="SESSION_SECRET", min_length=16)
    anthropic_api_key: str = Field(..., alias="ANTHROPIC_API_KEY")

    database_url: str = Field("sqlite:///./marketpulse.db", alias="DATABASE_URL")
    watchlist_recap_time: str = Field("16:30", alias="WATCHLIST_RECAP_TIME")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    ai_model: str = Field("claude-sonnet-4-6", alias="AI_MODEL")
    ai_cache_ttl_hours: int = Field(24, alias="AI_CACHE_TTL_HOURS")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/config.py tests/unit/test_config.py tests/unit/__init__.py
git commit -m "feat(config): typed settings via pydantic-settings"
```

---

### Task 3: Structured logging

**Files:**
- Create: `marketpulse/logging.py`
- Create: `tests/unit/test_logging.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_logging.py`:
```python
import json
import logging

from marketpulse.logging import configure_logging, get_logger


def test_logger_emits_json(capsys) -> None:
    configure_logging("INFO")
    log = get_logger("test")
    log.info("hello", ticker="AAPL", value=1)
    captured = capsys.readouterr()
    line = captured.out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "hello"
    assert payload["ticker"] == "AAPL"
    assert payload["value"] == 1
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/unit/test_logging.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `marketpulse/logging.py`**

```python
import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
```

- [ ] **Step 4: Run test to verify pass**

Run: `uv run pytest tests/unit/test_logging.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/logging.py tests/unit/test_logging.py
git commit -m "feat(logging): structured JSON logging via structlog"
```

---

## Phase 1 — Database

### Task 4: SQLAlchemy base, engine, session

**Files:**
- Create: `marketpulse/db/__init__.py`
- Create: `marketpulse/db/base.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create `marketpulse/db/__init__.py`** (empty)

- [ ] **Step 2: Implement `marketpulse/db/base.py`**

```python
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from marketpulse.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def init_engine(database_url: str | None = None) -> None:
    global _engine, _SessionLocal
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    _engine = create_engine(url, connect_args=connect_args, future=True)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


def session_scope() -> Iterator[Session]:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 3: Create `tests/conftest.py` with isolated-DB fixtures**

```python
import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

# Ensure required env vars exist before importing settings.
os.environ.setdefault("APP_PASSWORD_HASH", "$2b$12$abcdefghijklmnopqrstuv")
os.environ.setdefault("SESSION_SECRET", "x" * 32)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture()
def db_session(db_url: str) -> Session:
    from marketpulse.db import base as db_base
    from marketpulse.db.base import Base

    db_base.init_engine(db_url)
    Base.metadata.create_all(db_base.get_engine())
    gen = db_base.session_scope()
    session = next(gen)
    try:
        yield session
    finally:
        session.close()
```

- [ ] **Step 4: Verify imports**

Run: `uv run python -c "from marketpulse.db.base import Base; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/db tests/conftest.py
git commit -m "feat(db): SQLAlchemy base, engine, session scope"
```

---

### Task 5: ORM models

**Files:**
- Create: `marketpulse/db/models.py`
- Create: `tests/unit/test_models.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_models.py`:
```python
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from marketpulse.db.models import (
    AiAnalysis,
    AppSetting,
    DailyRecap,
    NewsCacheEntry,
    PriceCacheEntry,
    WatchlistItem,
)


def test_create_watchlist_item(db_session: Session) -> None:
    item = WatchlistItem(ticker="AAPL", notes="iphone")
    db_session.add(item)
    db_session.commit()
    assert item.id is not None
    assert item.added_at is not None


def test_daily_recap_unique_date(db_session: Session) -> None:
    today = datetime(2026, 5, 9).date()
    db_session.add(DailyRecap(recap_date=today, generation_status="pending"))
    db_session.commit()
    assert db_session.query(DailyRecap).count() == 1


def test_ai_analysis_with_expiry(db_session: Session) -> None:
    a = AiAnalysis(
        ticker="NVDA",
        model="claude-sonnet-4-6",
        prompt_version="v1",
        input_data_json="{}",
        response_markdown="hello",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db_session.add(a)
    db_session.commit()
    assert a.id is not None


def test_price_cache_composite_pk(db_session: Session) -> None:
    from datetime import date
    db_session.add(
        PriceCacheEntry(
            ticker="AAPL", date=date(2026, 5, 8),
            open=100, high=110, low=99, close=105, volume=1_000_000,
        )
    )
    db_session.commit()
    assert db_session.query(PriceCacheEntry).count() == 1


def test_news_cache_basic(db_session: Session) -> None:
    db_session.add(
        NewsCacheEntry(
            ticker="AAPL",
            headline="x",
            url="https://example.com",
            published_at=datetime.now(UTC),
            source="test",
        )
    )
    db_session.commit()
    assert db_session.query(NewsCacheEntry).count() == 1


def test_app_setting_kv(db_session: Session) -> None:
    db_session.add(AppSetting(key="foo", value="bar"))
    db_session.commit()
    got = db_session.query(AppSetting).filter_by(key="foo").one()
    assert got.value == "bar"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: FAIL — `marketpulse.db.models` missing.

- [ ] **Step 3: Implement `marketpulse/db/models.py`**

```python
from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from marketpulse.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class DailyRecap(Base):
    __tablename__ = "daily_recaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recap_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    market_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    watchlist_performance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    news_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_commentary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generation_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class AiAnalysis(Base):
    __tablename__ = "ai_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_data_json: Mapped[str] = mapped_column(Text, nullable=False)
    response_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_ai_analyses_ticker_expires", "ticker", "expires_at"),)


class PriceCacheEntry(Base):
    __tablename__ = "price_cache"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class NewsCacheEntry(Base):
    __tablename__ = "news_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_news_cache_ticker_date", "ticker", "published_at"),
        UniqueConstraint("ticker", "url", name="uq_news_ticker_url"),
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/db/models.py tests/unit/test_models.py
git commit -m "feat(db): ORM models for watchlist, recaps, analyses, caches"
```

---

### Task 6: Alembic initial migration

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/0001_initial.py`

- [ ] **Step 1: Generate alembic skeleton**

Run: `uv run alembic init alembic`
Expected: creates `alembic/` dir and `alembic.ini`. Replace generated files in next steps.

- [ ] **Step 2: Replace `alembic/env.py` contents**

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from marketpulse.config import get_settings
from marketpulse.db.base import Base
from marketpulse.db import models  # noqa: F401  ensure models are imported

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", get_settings().database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section) or {}
    cfg["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Generate the initial migration**

Run: `uv run alembic revision --autogenerate -m "initial schema"`
Expected: creates `alembic/versions/<hash>_initial_schema.py`. Rename it to `0001_initial.py` and set `revision = "0001"` and `down_revision = None` at the top.

- [ ] **Step 4: Apply migration to a temp DB**

Run: `DATABASE_URL=sqlite:///./test_migration.db uv run alembic upgrade head && rm test_migration.db`
Expected: prints `Running upgrade  -> 0001`. No errors.

- [ ] **Step 5: Commit**

```bash
git add alembic alembic.ini
git commit -m "feat(db): alembic initial migration"
```

---

## Phase 2 — Data Layer

### Task 7: Data types

**Files:**
- Create: `marketpulse/data/__init__.py` (empty)
- Create: `marketpulse/data/types.py`

- [ ] **Step 1: Implement `marketpulse/data/types.py`**

```python
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: float
    change_pct: float
    volume: int
    avg_volume_20d: int
    fetched_at: datetime
    stale: bool = False


@dataclass(frozen=True)
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class NewsItem:
    ticker: str
    headline: str
    url: str
    published_at: datetime
    source: str
    summary: str | None = None


@dataclass(frozen=True)
class Fundamentals:
    ticker: str
    market_cap: float | None
    pe_ratio: float | None
    eps: float | None
    sector: str | None
    industry: str | None


@dataclass(frozen=True)
class IndexQuote:
    symbol: str
    price: float
    change_pct: float


@dataclass(frozen=True)
class MarketOverview:
    spy: IndexQuote
    qqq: IndexQuote
    dia: IndexQuote
    vix: IndexQuote
    fetched_at: datetime
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "from marketpulse.data.types import Quote; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add marketpulse/data
git commit -m "feat(data): typed data classes for market data"
```

---

### Task 8: yfinance client (thin wrapper)

**Files:**
- Create: `marketpulse/data/yfinance_client.py`

This module is mock-replaced in tests; we deliberately do NOT unit-test it directly. We will test the higher-level service module that depends on it.

- [ ] **Step 1: Implement `marketpulse/data/yfinance_client.py`**

```python
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

import yfinance as yf

from marketpulse.data.types import Bar, Fundamentals, IndexQuote, MarketOverview, NewsItem, Quote


class YFinanceClient:
    """Thin wrapper around yfinance — the only module that imports yfinance.

    Replace via constructor injection in tests.
    """

    def fetch_quote(self, ticker: str) -> Quote:
        t = yf.Ticker(ticker)
        info = t.fast_info
        hist = t.history(period="21d", interval="1d")
        if hist.empty:
            raise ValueError(f"no data for {ticker}")
        last_close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else last_close
        change_pct = (last_close - prev_close) / prev_close * 100 if prev_close else 0.0
        avg_vol = int(hist["Volume"].tail(20).mean()) if len(hist) >= 5 else 0
        return Quote(
            ticker=ticker,
            price=float(info.last_price or last_close),
            change_pct=change_pct,
            volume=int(hist["Volume"].iloc[-1]),
            avg_volume_20d=avg_vol,
            fetched_at=datetime.now(UTC),
        )

    def fetch_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        hist = yf.Ticker(ticker).history(period=period, interval="1d")
        bars: list[Bar] = []
        for idx, row in hist.iterrows():
            bars.append(
                Bar(
                    date=idx.date() if hasattr(idx, "date") else idx,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row["Volume"]),
                )
            )
        return bars

    def fetch_news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        items = yf.Ticker(ticker).news or []
        out: list[NewsItem] = []
        for item in items[:limit]:
            ts = item.get("providerPublishTime")
            published = datetime.fromtimestamp(ts, tz=UTC) if ts else datetime.now(UTC)
            out.append(
                NewsItem(
                    ticker=ticker,
                    headline=item.get("title", ""),
                    url=item.get("link", ""),
                    published_at=published,
                    source=item.get("publisher", "unknown"),
                    summary=item.get("summary"),
                )
            )
        return out

    def fetch_fundamentals(self, ticker: str) -> Fundamentals:
        info = yf.Ticker(ticker).info or {}
        return Fundamentals(
            ticker=ticker,
            market_cap=info.get("marketCap"),
            pe_ratio=info.get("trailingPE"),
            eps=info.get("trailingEps"),
            sector=info.get("sector"),
            industry=info.get("industry"),
        )

    def fetch_market_overview(self) -> MarketOverview:
        symbols = ("SPY", "QQQ", "DIA", "^VIX")
        quotes: dict[str, IndexQuote] = {}
        for sym in symbols:
            q = self.fetch_quote(sym if sym != "^VIX" else "^VIX")
            key = sym.lstrip("^").lower()
            quotes[key] = IndexQuote(symbol=sym, price=q.price, change_pct=q.change_pct)
        return MarketOverview(
            spy=quotes["spy"],
            qqq=quotes["qqq"],
            dia=quotes["dia"],
            vix=quotes["vix"],
            fetched_at=datetime.now(UTC),
        )
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "from marketpulse.data.yfinance_client import YFinanceClient; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add marketpulse/data/yfinance_client.py
git commit -m "feat(data): yfinance client wrapper"
```

---

### Task 9: Price cache repository (TDD)

**Files:**
- Create: `marketpulse/data/cache.py`
- Create: `tests/unit/test_price_cache.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_price_cache.py`:
```python
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from marketpulse.data.cache import PriceCache
from marketpulse.data.types import Bar


def test_upsert_and_read(db_session: Session) -> None:
    cache = PriceCache(db_session)
    bars = [
        Bar(date=date(2026, 5, 6), open=1, high=2, low=0.5, close=1.5, volume=100),
        Bar(date=date(2026, 5, 7), open=1.5, high=2, low=1, close=1.8, volume=120),
    ]
    cache.upsert("AAPL", bars)
    got = cache.get_range("AAPL", date(2026, 5, 6), date(2026, 5, 7))
    assert [b.date for b in got] == [date(2026, 5, 6), date(2026, 5, 7)]
    assert got[0].close == 1.5


def test_upsert_idempotent_same_day(db_session: Session) -> None:
    cache = PriceCache(db_session)
    cache.upsert("AAPL", [Bar(date=date(2026, 5, 6), open=1, high=2, low=0.5, close=1.5, volume=100)])
    cache.upsert("AAPL", [Bar(date=date(2026, 5, 6), open=1, high=2, low=0.5, close=1.7, volume=200)])
    got = cache.get_range("AAPL", date(2026, 5, 6), date(2026, 5, 6))
    assert len(got) == 1
    assert got[0].close == 1.7
    assert got[0].volume == 200
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/unit/test_price_cache.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `marketpulse/data/cache.py` (price portion only for now)**

```python
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from marketpulse.data.types import Bar, NewsItem
from marketpulse.db.models import NewsCacheEntry, PriceCacheEntry


class PriceCache:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, ticker: str, bars: list[Bar]) -> None:
        if not bars:
            return
        rows = [
            {
                "ticker": ticker,
                "date": b.date,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "fetched_at": datetime.now(UTC),
            }
            for b in bars
        ]
        stmt = sqlite_insert(PriceCacheEntry).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "date"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
        self.session.execute(stmt)
        self.session.commit()

    def get_range(self, ticker: str, start: date, end: date) -> list[Bar]:
        stmt = (
            select(PriceCacheEntry)
            .where(PriceCacheEntry.ticker == ticker)
            .where(PriceCacheEntry.date >= start)
            .where(PriceCacheEntry.date <= end)
            .order_by(PriceCacheEntry.date)
        )
        rows = self.session.execute(stmt).scalars().all()
        return [
            Bar(date=r.date, open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume)
            for r in rows
        ]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_price_cache.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/data/cache.py tests/unit/test_price_cache.py
git commit -m "feat(data): price cache repository with upsert"
```

---

### Task 10: News cache repository (TDD)

**Files:**
- Modify: `marketpulse/data/cache.py`
- Create: `tests/unit/test_news_cache.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_news_cache.py`:
```python
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from marketpulse.data.cache import NewsCache
from marketpulse.data.types import NewsItem


def test_upsert_dedup_and_recent(db_session: Session) -> None:
    cache = NewsCache(db_session, ttl_days=7)
    now = datetime.now(UTC)
    items = [
        NewsItem(ticker="AAPL", headline="A", url="https://a.com", published_at=now, source="x"),
        NewsItem(ticker="AAPL", headline="B", url="https://b.com", published_at=now, source="x"),
    ]
    cache.upsert(items)
    cache.upsert(items)  # same urls -> no duplicates
    recent = cache.recent("AAPL", limit=10)
    assert len(recent) == 2
    assert {n.url for n in recent} == {"https://a.com", "https://b.com"}


def test_purge_expired(db_session: Session) -> None:
    cache = NewsCache(db_session, ttl_days=7)
    old = datetime.now(UTC) - timedelta(days=10)
    cache.upsert([NewsItem(ticker="A", headline="x", url="u", published_at=old, source="s")])
    cache.purge_expired()
    assert cache.recent("A", limit=10) == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/unit/test_news_cache.py -v`
Expected: FAIL — `NewsCache` missing.

- [ ] **Step 3: Append to `marketpulse/data/cache.py`**

```python
class NewsCache:
    def __init__(self, session: Session, ttl_days: int = 7) -> None:
        self.session = session
        self.ttl_days = ttl_days

    def upsert(self, items: list[NewsItem]) -> None:
        if not items:
            return
        rows = [
            {
                "ticker": i.ticker,
                "headline": i.headline,
                "url": i.url,
                "published_at": i.published_at,
                "source": i.source,
                "summary": i.summary,
            }
            for i in items
        ]
        stmt = sqlite_insert(NewsCacheEntry).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["ticker", "url"])
        self.session.execute(stmt)
        self.session.commit()

    def recent(self, ticker: str, limit: int) -> list[NewsItem]:
        cutoff = datetime.now(UTC) - timedelta(days=self.ttl_days)
        stmt = (
            select(NewsCacheEntry)
            .where(NewsCacheEntry.ticker == ticker)
            .where(NewsCacheEntry.published_at >= cutoff)
            .order_by(NewsCacheEntry.published_at.desc())
            .limit(limit)
        )
        rows = self.session.execute(stmt).scalars().all()
        return [
            NewsItem(
                ticker=r.ticker,
                headline=r.headline,
                url=r.url,
                published_at=r.published_at,
                source=r.source,
                summary=r.summary,
            )
            for r in rows
        ]

    def purge_expired(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(days=self.ttl_days)
        self.session.query(NewsCacheEntry).filter(NewsCacheEntry.published_at < cutoff).delete()
        self.session.commit()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_news_cache.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/data/cache.py tests/unit/test_news_cache.py
git commit -m "feat(data): news cache with dedup and TTL purge"
```

---

### Task 11: Data service — quote, history, news, market

**Files:**
- Create: `marketpulse/data/service.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_data_service.py`

- [ ] **Step 1: Write the failing test**

`tests/integration/test_data_service.py`:
```python
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from marketpulse.data.service import DataService
from marketpulse.data.types import Bar, Fundamentals, IndexQuote, MarketOverview, NewsItem, Quote


class FakeYF:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_quote = False

    def fetch_quote(self, ticker: str) -> Quote:
        self.calls.append(("quote", ticker))
        if self.fail_quote:
            raise RuntimeError("yfinance is down")
        return Quote(
            ticker=ticker, price=100.0, change_pct=1.0, volume=1000,
            avg_volume_20d=900, fetched_at=datetime.now(UTC),
        )

    def fetch_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        self.calls.append(("history", ticker))
        return [
            Bar(date=date(2026, 5, 6), open=1, high=2, low=0.5, close=1.5, volume=100),
            Bar(date=date(2026, 5, 7), open=1.5, high=2, low=1, close=1.8, volume=120),
        ]

    def fetch_news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        self.calls.append(("news", ticker))
        return [NewsItem(
            ticker=ticker, headline="x", url="https://a.com",
            published_at=datetime.now(UTC), source="s",
        )]

    def fetch_fundamentals(self, ticker: str) -> Fundamentals:
        self.calls.append(("fund", ticker))
        return Fundamentals(ticker=ticker, market_cap=1.0, pe_ratio=10.0,
                            eps=1.0, sector="Tech", industry="SW")

    def fetch_market_overview(self) -> MarketOverview:
        self.calls.append(("market", ""))
        q = lambda s: IndexQuote(symbol=s, price=100, change_pct=0.5)
        return MarketOverview(spy=q("SPY"), qqq=q("QQQ"), dia=q("DIA"),
                              vix=q("^VIX"), fetched_at=datetime.now(UTC))


def test_quote_passes_through(db_session: Session) -> None:
    yf = FakeYF()
    svc = DataService(db_session, yf)
    q = svc.get_quote("AAPL")
    assert q.ticker == "AAPL"
    assert q.price == 100.0
    assert not q.stale


def test_quote_falls_back_to_cache_on_failure(db_session: Session) -> None:
    yf = FakeYF()
    svc = DataService(db_session, yf)
    svc.get_history("AAPL", period="60d")  # populates cache
    yf.fail_quote = True
    q = svc.get_quote("AAPL")
    assert q.stale is True
    assert q.price > 0


def test_history_uses_cache_when_complete(db_session: Session) -> None:
    yf = FakeYF()
    svc = DataService(db_session, yf)
    svc.get_history("AAPL", period="60d")
    yf.calls.clear()
    bars = svc.get_history("AAPL", period="60d")
    assert ("history", "AAPL") not in yf.calls  # second call hits cache
    assert len(bars) == 2


def test_news_caches_and_returns(db_session: Session) -> None:
    yf = FakeYF()
    svc = DataService(db_session, yf)
    items = svc.get_news("AAPL", limit=5)
    assert len(items) == 1
    items2 = svc.get_news("AAPL", limit=5)
    # second call still hits yfinance (we always refresh news), but dedups
    assert len(items2) == 1


def test_market_overview(db_session: Session) -> None:
    svc = DataService(db_session, FakeYF())
    m = svc.get_market_overview()
    assert m.spy.symbol == "SPY"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/integration/test_data_service.py -v`
Expected: FAIL — `DataService` missing.

- [ ] **Step 3: Implement `marketpulse/data/service.py`**

```python
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from sqlalchemy.orm import Session

from marketpulse.data.cache import NewsCache, PriceCache
from marketpulse.data.types import Bar, Fundamentals, MarketOverview, NewsItem, Quote
from marketpulse.logging import get_logger

log = get_logger(__name__)


class _YFLike(Protocol):
    def fetch_quote(self, ticker: str) -> Quote: ...
    def fetch_history(self, ticker: str, period: str = ...) -> list[Bar]: ...
    def fetch_news(self, ticker: str, limit: int = ...) -> list[NewsItem]: ...
    def fetch_fundamentals(self, ticker: str) -> Fundamentals: ...
    def fetch_market_overview(self) -> MarketOverview: ...


class DataService:
    def __init__(self, session: Session, yf_client: _YFLike) -> None:
        self.session = session
        self.yf = yf_client
        self.price_cache = PriceCache(session)
        self.news_cache = NewsCache(session, ttl_days=7)

    def get_quote(self, ticker: str) -> Quote:
        try:
            return self.yf.fetch_quote(ticker)
        except Exception as exc:
            log.warning("quote_fallback_to_cache", ticker=ticker, error=str(exc))
            bars = self.price_cache.get_range(
                ticker, date.today() - timedelta(days=30), date.today()
            )
            if not bars:
                raise
            last, prev = bars[-1], bars[-2] if len(bars) > 1 else bars[-1]
            change_pct = ((last.close - prev.close) / prev.close * 100) if prev.close else 0.0
            return Quote(
                ticker=ticker,
                price=last.close,
                change_pct=change_pct,
                volume=last.volume,
                avg_volume_20d=int(sum(b.volume for b in bars[-20:]) / max(len(bars[-20:]), 1)),
                fetched_at=datetime.now(UTC),
                stale=True,
            )

    def get_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        days = int(period.rstrip("d")) if period.endswith("d") else 60
        end = date.today()
        start = end - timedelta(days=days)
        cached = self.price_cache.get_range(ticker, start, end)
        if cached and (end - cached[-1].date).days <= 1:
            return cached
        bars = self.yf.fetch_history(ticker, period=period)
        self.price_cache.upsert(ticker, bars)
        return bars

    def get_news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        try:
            fresh = self.yf.fetch_news(ticker, limit=limit)
            self.news_cache.upsert(fresh)
        except Exception as exc:
            log.warning("news_fetch_failed", ticker=ticker, error=str(exc))
        return self.news_cache.recent(ticker, limit=limit)

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        return self.yf.fetch_fundamentals(ticker)

    def get_market_overview(self) -> MarketOverview:
        return self.yf.fetch_market_overview()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/integration/test_data_service.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/data/service.py tests/integration/test_data_service.py tests/integration/__init__.py
git commit -m "feat(data): service layer with cache-first reads and stale fallback"
```

---

## Phase 3 — AI Layer

### Task 12: Prompt templates + version

**Files:**
- Create: `marketpulse/ai/__init__.py` (empty)
- Create: `marketpulse/ai/types.py`
- Create: `marketpulse/ai/prompts.py`
- Create: `tests/unit/test_prompts.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_prompts.py`:
```python
from datetime import UTC, datetime

from marketpulse.ai.prompts import (
    ANALYSIS_PROMPT_VERSION,
    COMMENTARY_PROMPT_VERSION,
    render_analysis_prompt,
    render_commentary_prompt,
)
from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote


def test_render_analysis_prompt_contains_data() -> None:
    quote = Quote(ticker="NVDA", price=900, change_pct=2.5, volume=1, avg_volume_20d=1,
                  fetched_at=datetime.now(UTC))
    fund = Fundamentals(ticker="NVDA", market_cap=1e12, pe_ratio=70, eps=12, sector="Tech",
                        industry="Semis")
    news = [NewsItem(ticker="NVDA", headline="Big news", url="u",
                     published_at=datetime.now(UTC), source="x")]
    bars = [Bar(date=datetime(2026, 5, 7).date(), open=1, high=2, low=0.5, close=1.5, volume=100)]
    out = render_analysis_prompt(quote=quote, fundamentals=fund, news=news, bars=bars)
    assert "NVDA" in out
    assert "Big news" in out
    assert ANALYSIS_PROMPT_VERSION  # non-empty


def test_render_commentary_prompt_with_recap_data() -> None:
    out = render_commentary_prompt(
        market_summary={"spy": 0.8, "qqq": 1.2, "vix": 14},
        watchlist_perf=[{"ticker": "AAPL", "change_pct": 1.2}],
    )
    assert "AAPL" in out
    assert COMMENTARY_PROMPT_VERSION
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/unit/test_prompts.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `marketpulse/ai/types.py`**

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AnalysisResult:
    ticker: str
    model: str
    prompt_version: str
    response_markdown: str
    requested_at: datetime
    cached: bool = False
```

- [ ] **Step 4: Implement `marketpulse/ai/prompts.py`**

```python
import json
from typing import Any

from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote

ANALYSIS_PROMPT_VERSION = "analysis-v1"
COMMENTARY_PROMPT_VERSION = "commentary-v1"

_ANALYSIS_SYSTEM = (
    "You are an equity research analyst. Produce a concise markdown report with three sections: "
    "## Fundamentals, ## Technicals, ## Risks. Use only the data provided. "
    "Do not invent figures. Do not give buy/sell recommendations."
)

_COMMENTARY_SYSTEM = (
    "You are a market recap writer. In one paragraph (3-5 sentences), summarize today's market "
    "for an investor watching this watchlist. Be factual, calm, and specific."
)


def render_analysis_prompt(
    *, quote: Quote, fundamentals: Fundamentals, news: list[NewsItem], bars: list[Bar]
) -> str:
    payload: dict[str, Any] = {
        "ticker": quote.ticker,
        "current": {
            "price": quote.price,
            "change_pct": round(quote.change_pct, 2),
            "volume": quote.volume,
            "avg_volume_20d": quote.avg_volume_20d,
        },
        "fundamentals": {
            "market_cap": fundamentals.market_cap,
            "pe_ratio": fundamentals.pe_ratio,
            "eps": fundamentals.eps,
            "sector": fundamentals.sector,
            "industry": fundamentals.industry,
        },
        "recent_bars": [
            {"date": b.date.isoformat(), "close": b.close, "volume": b.volume}
            for b in bars[-30:]
        ],
        "news": [
            {"headline": n.headline, "source": n.source,
             "published": n.published_at.isoformat(), "summary": n.summary}
            for n in news[:10]
        ],
    }
    return f"{_ANALYSIS_SYSTEM}\n\nDATA:\n{json.dumps(payload, indent=2)}"


def render_commentary_prompt(
    *, market_summary: dict[str, Any], watchlist_perf: list[dict[str, Any]]
) -> str:
    payload = {"market": market_summary, "watchlist": watchlist_perf}
    return f"{_COMMENTARY_SYSTEM}\n\nDATA:\n{json.dumps(payload, indent=2)}"
```

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_prompts.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add marketpulse/ai/__init__.py marketpulse/ai/types.py marketpulse/ai/prompts.py tests/unit/test_prompts.py
git commit -m "feat(ai): versioned prompt templates"
```

---

### Task 13: Anthropic client wrapper

**Files:**
- Create: `marketpulse/ai/client.py`

This module is mock-replaced in tests; not unit-tested directly.

- [ ] **Step 1: Implement `marketpulse/ai/client.py`**

```python
from typing import Protocol

import anthropic

from marketpulse.config import get_settings


class AiClient(Protocol):
    def complete(self, *, system: str, user: str) -> str: ...


class AnthropicClient:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self._client = anthropic.Anthropic(api_key=api_key or settings.anthropic_api_key)
        self._model = model or settings.ai_model

    def complete(self, *, system: str, user: str) -> str:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=[
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": user}],
        )
        parts: list[str] = []
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "".join(parts)
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "from marketpulse.ai.client import AnthropicClient; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add marketpulse/ai/client.py
git commit -m "feat(ai): Anthropic client wrapper with prompt caching"
```

---

### Task 14: AI service — analyze + commentary

**Files:**
- Create: `marketpulse/ai/service.py`
- Create: `tests/integration/test_ai_service.py`

- [ ] **Step 1: Write the failing test**

`tests/integration/test_ai_service.py`:
```python
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from marketpulse.ai.service import AiService
from marketpulse.data.types import Bar, Fundamentals, IndexQuote, MarketOverview, NewsItem, Quote


class FakeAi:
    def __init__(self, response: str = "## Fundamentals\nx\n## Technicals\ny\n## Risks\nz") -> None:
        self.response = response
        self.calls = 0

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        return self.response


class FakeData:
    def get_quote(self, ticker: str) -> Quote:
        return Quote(ticker=ticker, price=100, change_pct=1, volume=1, avg_volume_20d=1,
                     fetched_at=datetime.now(UTC))

    def get_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        return [Bar(date=date(2026, 5, 7), open=1, high=2, low=0.5, close=1.5, volume=100)]

    def get_news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        return [NewsItem(ticker=ticker, headline="x", url="u",
                         published_at=datetime.now(UTC), source="s")]

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        return Fundamentals(ticker=ticker, market_cap=1, pe_ratio=10, eps=1,
                            sector="t", industry="i")


def test_analyze_writes_cache(db_session: Session) -> None:
    ai = FakeAi()
    svc = AiService(db_session, ai_client=ai, data=FakeData(), model="m1", ttl_hours=24)
    res = svc.analyze("NVDA")
    assert res.ticker == "NVDA"
    assert "Fundamentals" in res.response_markdown
    assert ai.calls == 1
    res2 = svc.analyze("NVDA")
    assert res2.cached is True
    assert ai.calls == 1  # cache hit


def test_analyze_invalidates_on_prompt_version_change(db_session: Session, monkeypatch) -> None:
    ai = FakeAi()
    svc = AiService(db_session, ai_client=ai, data=FakeData(), model="m1", ttl_hours=24)
    svc.analyze("NVDA")
    monkeypatch.setattr("marketpulse.ai.prompts.ANALYSIS_PROMPT_VERSION", "analysis-v2")
    svc.analyze("NVDA")
    assert ai.calls == 2


def test_daily_commentary_passthrough(db_session: Session) -> None:
    ai = FakeAi(response="Markets were calm.")
    svc = AiService(db_session, ai_client=ai, data=FakeData(), model="m1", ttl_hours=24)
    text = svc.daily_commentary(
        market_summary={"spy": 0.8}, watchlist_perf=[{"ticker": "AAPL", "change_pct": 1}],
    )
    assert text == "Markets were calm."
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/integration/test_ai_service.py -v`
Expected: FAIL — service missing.

- [ ] **Step 3: Implement `marketpulse/ai/service.py`**

```python
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketpulse.ai import prompts
from marketpulse.ai.client import AiClient
from marketpulse.ai.types import AnalysisResult
from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote
from marketpulse.db.models import AiAnalysis


class _DataLike(Protocol):
    def get_quote(self, ticker: str) -> Quote: ...
    def get_history(self, ticker: str, period: str = ...) -> list[Bar]: ...
    def get_news(self, ticker: str, limit: int = ...) -> list[NewsItem]: ...
    def get_fundamentals(self, ticker: str) -> Fundamentals: ...


class AiService:
    def __init__(
        self,
        session: Session,
        *,
        ai_client: AiClient,
        data: _DataLike,
        model: str,
        ttl_hours: int,
    ) -> None:
        self.session = session
        self.ai = ai_client
        self.data = data
        self.model = model
        self.ttl_hours = ttl_hours

    def analyze(self, ticker: str) -> AnalysisResult:
        version = prompts.ANALYSIS_PROMPT_VERSION
        cached = self._lookup_cache(ticker, version)
        if cached:
            return AnalysisResult(
                ticker=ticker,
                model=cached.model,
                prompt_version=cached.prompt_version,
                response_markdown=cached.response_markdown,
                requested_at=cached.requested_at,
                cached=True,
            )

        quote = self.data.get_quote(ticker)
        fundamentals = self.data.get_fundamentals(ticker)
        bars = self.data.get_history(ticker, period="60d")
        news = self.data.get_news(ticker, limit=10)
        prompt_text = prompts.render_analysis_prompt(
            quote=quote, fundamentals=fundamentals, news=news, bars=bars,
        )
        response = self.ai.complete(system=prompt_text.split("\n\nDATA:\n")[0],
                                    user=prompt_text)
        now = datetime.now(UTC)
        record = AiAnalysis(
            ticker=ticker,
            model=self.model,
            prompt_version=version,
            input_data_json=json.dumps({"quote": quote.price, "n_news": len(news)}),
            response_markdown=response,
            requested_at=now,
            expires_at=now + timedelta(hours=self.ttl_hours),
        )
        self.session.add(record)
        self.session.commit()
        return AnalysisResult(
            ticker=ticker, model=self.model, prompt_version=version,
            response_markdown=response, requested_at=now, cached=False,
        )

    def daily_commentary(
        self, *, market_summary: dict[str, Any], watchlist_perf: list[dict[str, Any]]
    ) -> str:
        prompt_text = prompts.render_commentary_prompt(
            market_summary=market_summary, watchlist_perf=watchlist_perf,
        )
        return self.ai.complete(
            system=prompt_text.split("\n\nDATA:\n")[0], user=prompt_text,
        )

    def _lookup_cache(self, ticker: str, version: str) -> AiAnalysis | None:
        stmt = (
            select(AiAnalysis)
            .where(AiAnalysis.ticker == ticker)
            .where(AiAnalysis.prompt_version == version)
            .where(AiAnalysis.expires_at > datetime.now(UTC))
            .order_by(AiAnalysis.requested_at.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/integration/test_ai_service.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/ai/service.py tests/integration/test_ai_service.py
git commit -m "feat(ai): analyze with version-aware caching + commentary"
```

---

## Phase 4 — Recap

### Task 15: Signal computation (TDD)

**Files:**
- Create: `marketpulse/recap/__init__.py` (empty)
- Create: `marketpulse/recap/signals.py`
- Create: `tests/unit/test_signals.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_signals.py`:
```python
from datetime import date

from marketpulse.data.types import Bar, Quote
from marketpulse.recap.signals import detect_signals


def _bar(d: int, close: float, volume: int = 1_000_000) -> Bar:
    return Bar(date=date(2026, 5, d), open=close, high=close, low=close,
               close=close, volume=volume)


def test_big_move_signal() -> None:
    quote = Quote(ticker="X", price=110, change_pct=6.0, volume=1_000_000,
                  avg_volume_20d=1_000_000, fetched_at=date(2026, 5, 7))  # type: ignore[arg-type]
    bars = [_bar(i, 100) for i in range(1, 21)]
    sigs = detect_signals(quote, bars)
    assert "BIG_MOVE" in sigs


def test_volume_spike_signal() -> None:
    quote = Quote(ticker="X", price=100, change_pct=0.5, volume=3_000_000,
                  avg_volume_20d=1_000_000, fetched_at=date(2026, 5, 7))  # type: ignore[arg-type]
    bars = [_bar(i, 100) for i in range(1, 21)]
    sigs = detect_signals(quote, bars)
    assert "VOLUME_SPIKE" in sigs


def test_ma20_breakout_signal() -> None:
    bars = [_bar(i, 100) for i in range(1, 21)]
    bars.append(_bar(22, 110))  # last close above MA20=100
    quote = Quote(ticker="X", price=110, change_pct=1.0, volume=1_000_000,
                  avg_volume_20d=1_000_000, fetched_at=date(2026, 5, 7))  # type: ignore[arg-type]
    sigs = detect_signals(quote, bars)
    assert "MA20_BREAKOUT" in sigs


def test_no_signals_when_quiet() -> None:
    bars = [_bar(i, 100) for i in range(1, 21)]
    quote = Quote(ticker="X", price=100.5, change_pct=0.5, volume=900_000,
                  avg_volume_20d=1_000_000, fetched_at=date(2026, 5, 7))  # type: ignore[arg-type]
    sigs = detect_signals(quote, bars)
    assert sigs == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/unit/test_signals.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `marketpulse/recap/signals.py`**

```python
from marketpulse.data.types import Bar, Quote

BIG_MOVE_PCT = 5.0
VOLUME_SPIKE_RATIO = 2.0


def detect_signals(quote: Quote, bars: list[Bar]) -> list[str]:
    signals: list[str] = []
    if abs(quote.change_pct) >= BIG_MOVE_PCT:
        signals.append("BIG_MOVE")
    if quote.avg_volume_20d > 0 and quote.volume >= VOLUME_SPIKE_RATIO * quote.avg_volume_20d:
        signals.append("VOLUME_SPIKE")
    if len(bars) >= 21:
        ma20 = sum(b.close for b in bars[-21:-1]) / 20
        prev_close = bars[-2].close
        last_close = bars[-1].close
        if prev_close <= ma20 < last_close:
            signals.append("MA20_BREAKOUT")
    return signals
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_signals.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/recap/__init__.py marketpulse/recap/signals.py tests/unit/test_signals.py
git commit -m "feat(recap): signal detection (big move, volume spike, MA breakout)"
```

---

### Task 16: Recap generator service

**Files:**
- Create: `marketpulse/recap/service.py`
- Create: `tests/integration/test_recap_service.py`

- [ ] **Step 1: Write the failing test**

`tests/integration/test_recap_service.py`:
```python
import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from marketpulse.data.types import Bar, Fundamentals, IndexQuote, MarketOverview, NewsItem, Quote
from marketpulse.db.models import DailyRecap, WatchlistItem
from marketpulse.recap.service import RecapService


class FakeData:
    def __init__(self) -> None:
        self.fail_quote_for: set[str] = set()

    def get_market_overview(self) -> MarketOverview:
        q = lambda s: IndexQuote(symbol=s, price=100, change_pct=0.5)
        return MarketOverview(spy=q("SPY"), qqq=q("QQQ"), dia=q("DIA"),
                              vix=q("^VIX"), fetched_at=datetime.now(UTC))

    def get_quote(self, ticker: str) -> Quote:
        if ticker in self.fail_quote_for:
            raise RuntimeError("boom")
        return Quote(ticker=ticker, price=100, change_pct=1.0, volume=1_000_000,
                     avg_volume_20d=1_000_000, fetched_at=datetime.now(UTC))

    def get_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        return [Bar(date=date(2026, 5, 7), open=1, high=2, low=0.5, close=1.5, volume=100)] * 25

    def get_news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        return [NewsItem(ticker=ticker, headline=f"{ticker} news", url=f"https://x/{ticker}",
                         published_at=datetime.now(UTC), source="s")]


class FakeAi:
    def daily_commentary(self, *, market_summary, watchlist_perf) -> str:
        return "All good."


def test_generate_recap_success(db_session: Session) -> None:
    db_session.add_all([WatchlistItem(ticker="AAPL"), WatchlistItem(ticker="NVDA")])
    db_session.commit()
    svc = RecapService(db_session, data=FakeData(), ai=FakeAi())
    result = svc.generate(date(2026, 5, 8))
    assert result.generation_status == "success"
    assert result.ai_commentary_text == "All good."
    perf = json.loads(result.watchlist_performance_json)
    assert {p["ticker"] for p in perf} == {"AAPL", "NVDA"}


def test_generate_recap_idempotent(db_session: Session) -> None:
    db_session.add(WatchlistItem(ticker="AAPL"))
    db_session.commit()
    svc = RecapService(db_session, data=FakeData(), ai=FakeAi())
    svc.generate(date(2026, 5, 8))
    svc.generate(date(2026, 5, 8))
    assert db_session.query(DailyRecap).count() == 1


def test_partial_failure_marked_per_ticker(db_session: Session) -> None:
    db_session.add_all([WatchlistItem(ticker="AAPL"), WatchlistItem(ticker="BAD")])
    db_session.commit()
    fake = FakeData()
    fake.fail_quote_for = {"BAD"}
    svc = RecapService(db_session, data=fake, ai=FakeAi())
    result = svc.generate(date(2026, 5, 8))
    perf = json.loads(result.watchlist_performance_json)
    bad = next(p for p in perf if p["ticker"] == "BAD")
    assert bad["error"] is not None
    assert result.generation_status == "success"  # individual failure doesn't fail run


def test_complete_failure_when_market_data_unavailable(db_session: Session) -> None:
    class BadData(FakeData):
        def get_market_overview(self):
            raise RuntimeError("market is down")

    db_session.add(WatchlistItem(ticker="AAPL"))
    db_session.commit()
    svc = RecapService(db_session, data=BadData(), ai=FakeAi())
    result = svc.generate(date(2026, 5, 8))
    assert result.generation_status == "failed"
    assert "market is down" in (result.error_message or "")
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/integration/test_recap_service.py -v`
Expected: FAIL — `RecapService` missing.

- [ ] **Step 3: Implement `marketpulse/recap/service.py`**

```python
import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from marketpulse.data.types import Bar, MarketOverview, NewsItem, Quote
from marketpulse.db.models import DailyRecap, WatchlistItem
from marketpulse.logging import get_logger
from marketpulse.recap.signals import detect_signals

log = get_logger(__name__)


class _DataLike(Protocol):
    def get_market_overview(self) -> MarketOverview: ...
    def get_quote(self, ticker: str) -> Quote: ...
    def get_history(self, ticker: str, period: str = ...) -> list[Bar]: ...
    def get_news(self, ticker: str, limit: int = ...) -> list[NewsItem]: ...


class _AiLike(Protocol):
    def daily_commentary(self, *, market_summary: dict[str, Any],
                         watchlist_perf: list[dict[str, Any]]) -> str: ...


class RecapService:
    def __init__(self, session: Session, *, data: _DataLike, ai: _AiLike) -> None:
        self.session = session
        self.data = data
        self.ai = ai

    def generate(self, target: date) -> DailyRecap:
        recap = self._upsert_pending(target)
        try:
            market = self.data.get_market_overview()
            market_summary = {
                "spy": market.spy.change_pct,
                "qqq": market.qqq.change_pct,
                "dia": market.dia.change_pct,
                "vix": market.vix.price,
            }
            watch = self.session.query(WatchlistItem).order_by(WatchlistItem.sort_order).all()
            perf: list[dict[str, Any]] = []
            news_summary: list[dict[str, Any]] = []
            for item in watch:
                row = self._build_ticker_row(item.ticker)
                perf.append(row)
                if not row.get("error"):
                    news_summary.append({"ticker": item.ticker, "items": row.pop("news_items", [])})
            try:
                commentary = self.ai.daily_commentary(
                    market_summary=market_summary, watchlist_perf=perf,
                )
            except Exception as exc:
                log.warning("commentary_failed", error=str(exc))
                commentary = f"AI commentary unavailable ({exc})."

            recap.market_summary_json = json.dumps(market_summary)
            recap.watchlist_performance_json = json.dumps(perf)
            recap.news_summary_json = json.dumps(news_summary)
            recap.ai_commentary_text = commentary
            recap.generation_status = "success"
            recap.error_message = None
            recap.generated_at = datetime.now(UTC)
        except Exception as exc:
            log.error("recap_failed", date=str(target), error=str(exc))
            recap.generation_status = "failed"
            recap.error_message = str(exc)
            recap.generated_at = datetime.now(UTC)
        self.session.commit()
        return recap

    def _upsert_pending(self, target: date) -> DailyRecap:
        existing = (
            self.session.query(DailyRecap).filter(DailyRecap.recap_date == target).one_or_none()
        )
        if existing:
            existing.generation_status = "pending"
            existing.error_message = None
            self.session.commit()
            return existing
        rec = DailyRecap(recap_date=target, generation_status="pending")
        self.session.add(rec)
        self.session.commit()
        return rec

    def _build_ticker_row(self, ticker: str) -> dict[str, Any]:
        try:
            quote = self.data.get_quote(ticker)
            bars = self.data.get_history(ticker, period="60d")
            news = self.data.get_news(ticker, limit=5)
            signals = detect_signals(quote, bars)
            return {
                "ticker": ticker,
                "price": quote.price,
                "change_pct": round(quote.change_pct, 2),
                "volume": quote.volume,
                "avg_volume_20d": quote.avg_volume_20d,
                "stale": quote.stale,
                "signals": signals,
                "error": None,
                "news_items": [
                    {"headline": n.headline, "url": n.url, "source": n.source,
                     "published_at": n.published_at.isoformat()}
                    for n in news
                ],
            }
        except Exception as exc:
            log.warning("ticker_row_failed", ticker=ticker, error=str(exc))
            return {"ticker": ticker, "error": str(exc), "signals": []}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/integration/test_recap_service.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/recap/service.py tests/integration/test_recap_service.py
git commit -m "feat(recap): daily recap service with idempotency and partial failure handling"
```

---

## Phase 5 — Auth

### Task 17: Password hashing/verification

**Files:**
- Create: `marketpulse/auth/__init__.py` (empty)
- Create: `marketpulse/auth/password.py`
- Create: `tests/unit/test_password.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_password.py`:
```python
from marketpulse.auth.password import hash_password, verify_password


def test_hash_and_verify() -> None:
    h = hash_password("hunter2")
    assert verify_password("hunter2", h)
    assert not verify_password("wrong", h)


def test_hash_returns_different_each_time() -> None:
    assert hash_password("a") != hash_password("a")
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/unit/test_password.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `marketpulse/auth/password.py`**

```python
import bcrypt


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_password.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/auth/__init__.py marketpulse/auth/password.py tests/unit/test_password.py
git commit -m "feat(auth): bcrypt password hashing"
```

---

### Task 18: Signed-cookie session helpers

**Files:**
- Create: `marketpulse/auth/session.py`
- Create: `tests/unit/test_session.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_session.py`:
```python
import pytest

from marketpulse.auth.session import SESSION_COOKIE, SessionManager


def test_roundtrip() -> None:
    mgr = SessionManager(secret="x" * 32)
    token = mgr.issue()
    assert mgr.verify(token) is True


def test_tampered_token_rejected() -> None:
    mgr = SessionManager(secret="x" * 32)
    token = mgr.issue()
    bad = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    assert mgr.verify(bad) is False


def test_expired_token_rejected() -> None:
    mgr = SessionManager(secret="x" * 32)
    token = mgr.issue()
    assert mgr.verify(token, max_age_seconds=0) is False


def test_cookie_constant_present() -> None:
    assert SESSION_COOKIE
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/unit/test_session.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `marketpulse/auth/session.py`**

```python
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

SESSION_COOKIE = "mp_session"
DEFAULT_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


class SessionManager:
    def __init__(self, secret: str) -> None:
        self._signer = TimestampSigner(secret)

    def issue(self) -> str:
        return self._signer.sign("auth").decode("utf-8")

    def verify(self, token: str, max_age_seconds: int = DEFAULT_MAX_AGE) -> bool:
        try:
            self._signer.unsign(token, max_age=max_age_seconds)
            return True
        except (BadSignature, SignatureExpired):
            return False
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_session.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/auth/session.py tests/unit/test_session.py
git commit -m "feat(auth): signed-cookie session manager"
```

---

## Phase 6 — Web

### Task 19: FastAPI app factory + base template

**Files:**
- Create: `marketpulse/web/__init__.py` (empty)
- Create: `marketpulse/web/main.py`
- Create: `marketpulse/web/deps.py`
- Create: `marketpulse/web/routes/__init__.py` (empty)
- Create: `marketpulse/web/routes/health.py`
- Create: `marketpulse/web/templates/base.html`
- Create: `tests/web/__init__.py`
- Create: `tests/web/test_health.py`

- [ ] **Step 1: Write the failing test**

`tests/web/test_health.py`:
```python
from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
```

- [ ] **Step 2: Add `client` fixture to `tests/conftest.py`**

Append to existing `tests/conftest.py`:
```python
@pytest.fixture()
def client(db_url: str):
    from fastapi.testclient import TestClient

    from marketpulse.db import base as db_base
    from marketpulse.db.base import Base
    from marketpulse.web.main import create_app

    db_base.init_engine(db_url)
    Base.metadata.create_all(db_base.get_engine())
    app = create_app()
    with TestClient(app) as c:
        yield c
```

- [ ] **Step 3: Run test to verify failure**

Run: `uv run pytest tests/web/test_health.py -v`
Expected: FAIL — `create_app` missing.

- [ ] **Step 4: Implement `marketpulse/web/main.py`**

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from marketpulse.config import get_settings
from marketpulse.logging import configure_logging

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="MarketPulse")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from marketpulse.web.routes import health  # noqa: WPS433
    app.include_router(health.router)
    return app


app = create_app()
```

- [ ] **Step 5: Implement `marketpulse/web/routes/health.py`**

```python
from fastapi import APIRouter

from marketpulse.db.base import get_engine

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    engine = get_engine()
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    return {"status": "ok"}
```

- [ ] **Step 6: Implement `marketpulse/web/templates/base.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>MarketPulse</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="stylesheet" href="/static/app.css" />
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
  <script src="https://unpkg.com/htmx-ext-sse@2.2.2"></script>
</head>
<body class="bg-slate-50 text-slate-900 font-sans">
  <header class="border-b bg-white">
    <nav class="max-w-5xl mx-auto px-4 py-3 flex gap-4 text-sm">
      <a href="/" class="font-semibold">MarketPulse</a>
      <a href="/watchlist">Watchlist</a>
      <a href="/recaps">Recaps</a>
    </nav>
  </header>
  <main class="max-w-5xl mx-auto p-4">
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 7: Create empty static dir to satisfy mount**

```bash
mkdir -p marketpulse/web/static
touch marketpulse/web/static/.gitkeep
```

- [ ] **Step 8: Run test to verify pass**

Run: `uv run pytest tests/web/test_health.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add marketpulse/web tests/web tests/conftest.py
git commit -m "feat(web): FastAPI app factory, base template, /health"
```

---

### Task 20: Auth dependency + login routes

**Files:**
- Modify: `marketpulse/web/deps.py`
- Create: `marketpulse/web/routes/auth.py`
- Create: `marketpulse/web/templates/login.html`
- Modify: `marketpulse/web/main.py`
- Create: `tests/web/test_auth.py`

- [ ] **Step 1: Write the failing test**

`tests/web/test_auth.py`:
```python
import os

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password


def test_unauthenticated_redirected(client: TestClient) -> None:
    res = client.get("/", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"].endswith("/login")


def test_login_success_sets_cookie(client: TestClient, monkeypatch) -> None:
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    res = client.post("/login", data={"password": pw}, follow_redirects=False)
    assert res.status_code in (302, 303)
    assert "mp_session" in res.cookies


def test_login_failure(client: TestClient) -> None:
    res = client.post("/login", data={"password": "wrong"})
    assert res.status_code == 401
```

- [ ] **Step 2: Implement `marketpulse/web/deps.py`**

```python
from collections.abc import Iterator

from fastapi import Cookie, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from marketpulse.auth.session import SESSION_COOKIE, SessionManager
from marketpulse.config import get_settings
from marketpulse.db.base import session_scope


def get_db() -> Iterator[Session]:
    yield from session_scope()


def get_session_manager() -> SessionManager:
    return SessionManager(secret=get_settings().session_secret)


def require_auth(
    request: Request,
    mp_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> None:
    mgr = get_session_manager()
    if not mp_session or not mgr.verify(mp_session):
        # API endpoints expect 401, page routes expect redirect — handled in routes
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="auth required")
```

- [ ] **Step 3: Implement `marketpulse/web/routes/auth.py`**

```python
from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from marketpulse.auth.password import verify_password
from marketpulse.auth.session import SESSION_COOKIE
from marketpulse.config import get_settings
from marketpulse.web.deps import get_session_manager
from marketpulse.web.main import templates

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
def login(password: str = Form(...)):
    settings = get_settings()
    if not verify_password(password, settings.app_password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad password")
    token = get_session_manager().issue()
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
```

- [ ] **Step 4: Implement `marketpulse/web/templates/login.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="max-w-sm mx-auto mt-16 bg-white p-6 rounded-md shadow-sm">
  <h1 class="text-xl font-semibold mb-4">Login</h1>
  <form method="post" action="/login" class="flex flex-col gap-3">
    <input type="password" name="password" placeholder="Password" required
           class="border rounded px-3 py-2" autofocus />
    <button class="bg-slate-900 text-white py-2 rounded">Sign in</button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 5: Wire auth router and a redirect-on-unauth at app level**

Modify `marketpulse/web/main.py` `create_app` body — replace the include block:
```python
    from marketpulse.web.routes import auth, health  # noqa: WPS433
    app.include_router(health.router)
    app.include_router(auth.router)

    @app.exception_handler(Exception)
    async def _redirect_unauth(request, exc):  # noqa: ANN001
        from fastapi import HTTPException
        from fastapi.responses import RedirectResponse, JSONResponse
        if isinstance(exc, HTTPException) and exc.status_code == 401:
            if request.url.path.startswith("/login") or request.url.path.startswith("/health"):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            accept = request.headers.get("accept", "")
            if "text/html" in accept or accept == "":
                return RedirectResponse(url="/login", status_code=303)
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        raise exc

    return app
```

- [ ] **Step 6: Run tests to verify pass**

Run: `uv run pytest tests/web/test_auth.py -v`
Expected: 3 PASS. (`test_unauthenticated_redirected` requires a `/` route that uses `require_auth` — implemented in next task; if it fails here, defer that single test until Task 21 lands.)

- [ ] **Step 7: Commit**

```bash
git add marketpulse/web tests/web/test_auth.py
git commit -m "feat(web): login/logout + auth dependency + redirect handler"
```

---

### Task 21: Home page route

**Files:**
- Create: `marketpulse/web/routes/home.py`
- Create: `marketpulse/web/templates/home.html`
- Create: `marketpulse/web/templates/partials/recap_card.html`
- Modify: `marketpulse/web/main.py` (include router)

- [ ] **Step 1: Implement `marketpulse/web/routes/home.py`**

```python
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from marketpulse.db.base import session_scope
from marketpulse.db.models import DailyRecap, WatchlistItem
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()


@router.get("/")
def home(request: Request, db: Session = Depends(get_db), _: None = Depends(require_auth)):
    today = date.today()
    recap = db.query(DailyRecap).filter(DailyRecap.recap_date == today).one_or_none()
    items = db.query(WatchlistItem).order_by(WatchlistItem.sort_order).all()
    return templates.TemplateResponse(
        request, "home.html",
        {"recap": recap, "watchlist": items, "today": today},
    )
```

- [ ] **Step 2: Implement `marketpulse/web/templates/home.html`**

```html
{% extends "base.html" %}
{% block content %}
{% include "partials/recap_card.html" %}

<section class="mt-6 bg-white rounded-md shadow-sm">
  <header class="flex items-center justify-between p-4 border-b">
    <h2 class="font-semibold">Watchlist</h2>
    <a href="/watchlist" class="text-sm text-blue-600">Manage</a>
  </header>
  <table class="w-full text-sm">
    <thead class="text-left text-slate-500">
      <tr><th class="px-4 py-2">Ticker</th><th>Price</th><th>Δ%</th><th>Vol</th><th>Signals</th></tr>
    </thead>
    <tbody>
      {% for item in watchlist %}
      <tr class="border-t hover:bg-slate-50">
        <td class="px-4 py-2"><a href="/stock/{{ item.ticker }}" class="font-medium">{{ item.ticker }}</a></td>
        <td>—</td><td>—</td><td>—</td><td>—</td>
      </tr>
      {% endfor %}
      {% if not watchlist %}
      <tr><td colspan="5" class="px-4 py-8 text-center text-slate-500">
        No tickers yet. <a href="/watchlist" class="text-blue-600">Add some</a>.
      </td></tr>
      {% endif %}
    </tbody>
  </table>
</section>
{% endblock %}
```

(Live prices on the home table are intentionally `—` for v1's first cut; Task 24's per-stock page shows real data. Adding live quotes here would require batched yfinance calls per page-view; deferred.)

- [ ] **Step 3: Implement `marketpulse/web/templates/partials/recap_card.html`**

```html
<section class="bg-white rounded-md shadow-sm p-4">
  <header class="flex items-center justify-between">
    <h2 class="font-semibold">Today's Recap — {{ today }}</h2>
  </header>
  {% if not recap %}
    <p class="mt-2 text-slate-500">No recap yet for today.</p>
  {% elif recap.generation_status == "pending" %}
    <p class="mt-2 text-slate-500"
       hx-get="/" hx-trigger="every 10s" hx-target="body">Generating recap…</p>
  {% elif recap.generation_status == "failed" %}
    <p class="mt-2 text-red-600">Recap failed: {{ recap.error_message }}</p>
    <form method="post" action="/recap/{{ recap.recap_date }}/retry">
      <button class="mt-2 bg-slate-900 text-white px-3 py-1 rounded">Retry</button>
    </form>
  {% else %}
    <p class="mt-2 whitespace-pre-line">{{ recap.ai_commentary_text }}</p>
    <a class="text-sm text-blue-600" href="/recap/{{ recap.recap_date }}">Full recap →</a>
  {% endif %}
</section>
```

- [ ] **Step 4: Wire router**

Modify `marketpulse/web/main.py` to also `from marketpulse.web.routes import home` and `app.include_router(home.router)`.

- [ ] **Step 5: Verify all auth tests now pass**

Run: `uv run pytest tests/web/test_auth.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/web
git commit -m "feat(web): home page with recap card and watchlist table"
```

---

### Task 22: Watchlist routes

**Files:**
- Create: `marketpulse/web/routes/watchlist.py`
- Create: `marketpulse/web/templates/watchlist.html`
- Create: `marketpulse/web/templates/partials/watchlist_row.html`
- Modify: `marketpulse/web/main.py` (include router)
- Create: `tests/web/test_watchlist.py`

- [ ] **Step 1: Write the failing test**

`tests/web/test_watchlist.py`:
```python
import os

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password


def _login(client: TestClient, monkeypatch) -> None:
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_add_and_list_watchlist(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    res = client.post("/watchlist", data={"ticker": "AAPL"})
    assert res.status_code == 200
    assert "AAPL" in res.text
    page = client.get("/watchlist")
    assert "AAPL" in page.text


def test_delete_watchlist_item(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    client.post("/watchlist", data={"ticker": "TSLA"})
    page = client.get("/watchlist")
    # crude: read the row id by querying DB through engine
    from marketpulse.db.base import get_engine
    from sqlalchemy import text
    with get_engine().connect() as conn:
        row_id = conn.execute(text("SELECT id FROM watchlist_items WHERE ticker='TSLA'")).scalar_one()
    res = client.delete(f"/watchlist/{row_id}")
    assert res.status_code == 200
    assert "TSLA" not in client.get("/watchlist").text


def test_invalid_ticker_rejected(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    res = client.post("/watchlist", data={"ticker": "  "})
    assert res.status_code == 422
```

- [ ] **Step 2: Implement `marketpulse/web/routes/watchlist.py`**

```python
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.db.models import WatchlistItem
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


@router.get("/watchlist", response_class=HTMLResponse)
def watchlist_page(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    items = db.query(WatchlistItem).order_by(WatchlistItem.sort_order, WatchlistItem.id).all()
    return templates.TemplateResponse(request, "watchlist.html", {"items": items})


@router.post("/watchlist", response_class=HTMLResponse)
def watchlist_add(
    request: Request,
    ticker: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    normalized = ticker.strip().upper()
    if not _TICKER_RE.match(normalized):
        raise HTTPException(status_code=422, detail="invalid ticker")
    existing = db.query(WatchlistItem).filter(WatchlistItem.ticker == normalized).one_or_none()
    if existing:
        item = existing
    else:
        item = WatchlistItem(ticker=normalized)
        db.add(item)
        db.commit()
        db.refresh(item)
    return templates.TemplateResponse(request, "partials/watchlist_row.html", {"item": item})


@router.delete("/watchlist/{item_id}", response_class=HTMLResponse)
def watchlist_delete(
    item_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    db.query(WatchlistItem).filter(WatchlistItem.id == item_id).delete()
    db.commit()
    return HTMLResponse("")  # HTMX swaps an empty fragment, removing the row
```

- [ ] **Step 3: Implement templates**

`marketpulse/web/templates/watchlist.html`:
```html
{% extends "base.html" %}
{% block content %}
<section class="bg-white rounded-md shadow-sm p-4">
  <h1 class="font-semibold mb-3">Watchlist</h1>

  <form hx-post="/watchlist" hx-target="#watchlist-rows" hx-swap="beforeend"
        class="flex gap-2 mb-4">
    <input name="ticker" placeholder="AAPL" required
           class="border rounded px-3 py-1 uppercase" />
    <button class="bg-slate-900 text-white px-3 py-1 rounded">Add</button>
  </form>

  <table class="w-full text-sm">
    <thead class="text-left text-slate-500">
      <tr><th class="px-2 py-1">Ticker</th><th>Notes</th><th></th></tr>
    </thead>
    <tbody id="watchlist-rows">
      {% for item in items %}{% include "partials/watchlist_row.html" %}{% endfor %}
    </tbody>
  </table>
</section>
{% endblock %}
```

`marketpulse/web/templates/partials/watchlist_row.html`:
```html
<tr id="watchlist-row-{{ item.id }}" class="border-t">
  <td class="px-2 py-1"><a href="/stock/{{ item.ticker }}" class="font-medium">{{ item.ticker }}</a></td>
  <td>{{ item.notes or "" }}</td>
  <td class="text-right">
    <button hx-delete="/watchlist/{{ item.id }}"
            hx-target="#watchlist-row-{{ item.id }}" hx-swap="outerHTML"
            class="text-red-600 text-xs">Remove</button>
  </td>
</tr>
```

- [ ] **Step 4: Wire router**

Modify `marketpulse/web/main.py` to include `watchlist.router`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/web/test_watchlist.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/web tests/web/test_watchlist.py
git commit -m "feat(web): watchlist CRUD with HTMX fragments"
```

---

### Task 23: Service factories (DI for routes)

**Files:**
- Modify: `marketpulse/web/deps.py`

The next routes need a `DataService`, `AiService`, and `RecapService`. Centralize their construction.

- [ ] **Step 1: Append to `marketpulse/web/deps.py`**

```python
from marketpulse.ai.client import AnthropicClient
from marketpulse.ai.service import AiService
from marketpulse.config import get_settings as _get_settings
from marketpulse.data.service import DataService
from marketpulse.data.yfinance_client import YFinanceClient
from marketpulse.recap.service import RecapService


def get_data_service(db: Session = Depends(get_db)) -> DataService:
    return DataService(db, YFinanceClient())


def get_ai_service(
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
) -> AiService:
    s = _get_settings()
    return AiService(
        db, ai_client=AnthropicClient(), data=data,
        model=s.ai_model, ttl_hours=s.ai_cache_ttl_hours,
    )


def get_recap_service(
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    ai: AiService = Depends(get_ai_service),
) -> RecapService:
    # Provide ai with a `daily_commentary` shim — RecapService expects that protocol.
    return RecapService(db, data=data, ai=ai)
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "from marketpulse.web.deps import get_recap_service; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add marketpulse/web/deps.py
git commit -m "feat(web): DI factories for data/ai/recap services"
```

---

### Task 24: Per-stock page + AI analysis route

**Files:**
- Create: `marketpulse/web/routes/stock.py`
- Create: `marketpulse/web/templates/stock.html`
- Create: `marketpulse/web/templates/partials/analysis_block.html`
- Modify: `marketpulse/web/main.py` (include router)
- Create: `tests/web/test_stock.py`

- [ ] **Step 1: Write the failing test**

`tests/web/test_stock.py`:
```python
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from marketpulse.ai.types import AnalysisResult
from marketpulse.auth.password import hash_password
from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


class _FakeData:
    def get_quote(self, ticker):
        return Quote(ticker=ticker, price=100, change_pct=1, volume=10,
                     avg_volume_20d=10, fetched_at=datetime.now(UTC))
    def get_history(self, ticker, period="60d"):
        return [Bar(date=date(2026, 5, 7), open=1, high=2, low=0.5, close=1.5, volume=100)]
    def get_news(self, ticker, limit=10):
        return [NewsItem(ticker=ticker, headline="hello", url="u",
                         published_at=datetime.now(UTC), source="s")]
    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, market_cap=1, pe_ratio=10, eps=1,
                            sector="t", industry="i")


class _FakeAi:
    def analyze(self, ticker):
        return AnalysisResult(
            ticker=ticker, model="m", prompt_version="v",
            response_markdown="## Fundamentals\nstuff", requested_at=datetime.now(UTC),
        )


def test_stock_page(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    try:
        res = client.get("/stock/AAPL")
        assert res.status_code == 200
        assert "AAPL" in res.text
    finally:
        client.app.dependency_overrides.clear()


def test_stock_analyze(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    from marketpulse.web.deps import get_ai_service, get_data_service
    client.app.dependency_overrides[get_data_service] = lambda: _FakeData()
    client.app.dependency_overrides[get_ai_service] = lambda: _FakeAi()
    try:
        res = client.post("/stock/AAPL/analyze")
        assert res.status_code == 200
        assert "Fundamentals" in res.text
    finally:
        client.app.dependency_overrides.clear()
```

- [ ] **Step 2: Implement `marketpulse/web/routes/stock.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from marketpulse.ai.service import AiService
from marketpulse.data.service import DataService
from marketpulse.web.deps import get_ai_service, get_data_service, require_auth
from marketpulse.web.main import templates

router = APIRouter()


@router.get("/stock/{ticker}", response_class=HTMLResponse)
def stock_page(
    request: Request,
    ticker: str,
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    ticker = ticker.upper()
    try:
        quote = data.get_quote(ticker)
        bars = data.get_history(ticker, period="60d")
        news = data.get_news(ticker, limit=5)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return templates.TemplateResponse(
        request, "stock.html",
        {"ticker": ticker, "quote": quote, "bars": bars, "news": news},
    )


@router.post("/stock/{ticker}/analyze", response_class=HTMLResponse)
def stock_analyze(
    request: Request,
    ticker: str,
    ai: AiService = Depends(get_ai_service),
    _: None = Depends(require_auth),
):
    ticker = ticker.upper()
    result = ai.analyze(ticker)
    return templates.TemplateResponse(
        request, "partials/analysis_block.html",
        {"ticker": ticker, "result": result},
    )
```

- [ ] **Step 3: Implement templates**

`marketpulse/web/templates/stock.html`:
```html
{% extends "base.html" %}
{% block content %}
<section class="bg-white rounded-md shadow-sm p-4">
  <header class="flex items-baseline justify-between">
    <h1 class="text-xl font-semibold">{{ ticker }}</h1>
    <span class="text-2xl">{{ "%.2f"|format(quote.price) }}
      <span class="{% if quote.change_pct >= 0 %}text-green-600{% else %}text-red-600{% endif %}">
        {{ "%+.2f"|format(quote.change_pct) }}%
      </span>
    </span>
  </header>
  {% if quote.stale %}<p class="text-amber-600 text-xs">⚠ Showing cached data</p>{% endif %}

  <h2 class="mt-6 font-semibold">Recent News</h2>
  <ul class="mt-2 text-sm space-y-1">
    {% for n in news %}
    <li><a href="{{ n.url }}" class="text-blue-600">{{ n.headline }}</a>
        <span class="text-slate-500">— {{ n.source }}</span></li>
    {% endfor %}
  </ul>

  <div id="analysis" class="mt-6">
    <button hx-post="/stock/{{ ticker }}/analyze" hx-target="#analysis" hx-swap="innerHTML"
            class="bg-slate-900 text-white px-3 py-1 rounded">AI Deep Analysis</button>
  </div>
</section>
{% endblock %}
```

`marketpulse/web/templates/partials/analysis_block.html`:
```html
<article class="prose prose-sm max-w-none mt-3 p-3 bg-slate-50 rounded">
  <small class="text-slate-500">
    Generated {% if result.cached %}(cached){% else %}(fresh){% endif %} •
    model {{ result.model }} • prompt {{ result.prompt_version }}
  </small>
  <div>{{ result.response_markdown | replace("\n", "<br>") | safe }}</div>
</article>
```

- [ ] **Step 4: Wire router**

Modify `marketpulse/web/main.py` to include `stock.router`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/web/test_stock.py -v`
Expected: 2 PASS.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/web tests/web/test_stock.py
git commit -m "feat(web): per-stock detail page and AI analysis route"
```

---

### Task 25: Recap routes (view + retry)

**Files:**
- Create: `marketpulse/web/routes/recap.py`
- Create: `marketpulse/web/templates/recap.html`
- Create: `marketpulse/web/templates/recaps.html`
- Modify: `marketpulse/web/main.py` (include router)
- Create: `tests/web/test_recap.py`

- [ ] **Step 1: Write the failing test**

`tests/web/test_recap.py`:
```python
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.base import session_scope
from marketpulse.db.models import DailyRecap


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed_recap(d: date, status: str = "success") -> None:
    gen = session_scope()
    db = next(gen)
    try:
        db.add(DailyRecap(
            recap_date=d, generation_status=status,
            ai_commentary_text="ok",
            generated_at=datetime.now(UTC),
        ))
        db.commit()
    finally:
        db.close()


def test_recap_detail(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    _seed_recap(date(2026, 5, 8))
    res = client.get("/recap/2026-05-08")
    assert res.status_code == 200
    assert "2026-05-08" in res.text


def test_recap_list(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    _seed_recap(date(2026, 5, 7))
    _seed_recap(date(2026, 5, 8))
    res = client.get("/recaps")
    assert res.status_code == 200
    assert "2026-05-07" in res.text and "2026-05-08" in res.text
```

- [ ] **Step 2: Implement `marketpulse/web/routes/recap.py`**

```python
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from marketpulse.db.models import DailyRecap
from marketpulse.recap.service import RecapService
from marketpulse.web.deps import get_db, get_recap_service, require_auth
from marketpulse.web.main import templates

router = APIRouter()


@router.get("/recaps", response_class=HTMLResponse)
def recap_list(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    rows = db.query(DailyRecap).order_by(DailyRecap.recap_date.desc()).limit(60).all()
    return templates.TemplateResponse(request, "recaps.html", {"rows": rows})


@router.get("/recap/{recap_date}", response_class=HTMLResponse)
def recap_detail(
    request: Request,
    recap_date: date,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    row = db.query(DailyRecap).filter(DailyRecap.recap_date == recap_date).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return templates.TemplateResponse(request, "recap.html", {"row": row})


@router.post("/recap/{recap_date}/retry")
def recap_retry(
    recap_date: date,
    svc: RecapService = Depends(get_recap_service),
    _: None = Depends(require_auth),
):
    svc.generate(recap_date)
    return RedirectResponse(url=f"/recap/{recap_date}", status_code=303)
```

- [ ] **Step 3: Implement templates**

`marketpulse/web/templates/recap.html`:
```html
{% extends "base.html" %}
{% block content %}
<section class="bg-white rounded-md shadow-sm p-4">
  <h1 class="font-semibold mb-2">Recap — {{ row.recap_date }}</h1>
  <p class="text-xs text-slate-500">Status: {{ row.generation_status }}
    {% if row.generated_at %}— generated {{ row.generated_at }}{% endif %}</p>
  {% if row.error_message %}
    <p class="mt-2 text-red-600">Error: {{ row.error_message }}</p>
  {% endif %}
  {% if row.ai_commentary_text %}
    <p class="mt-3 whitespace-pre-line">{{ row.ai_commentary_text }}</p>
  {% endif %}
  {% if row.market_summary_json %}
    <h2 class="mt-4 font-semibold">Market</h2>
    <pre class="text-xs bg-slate-50 p-2 rounded">{{ row.market_summary_json }}</pre>
  {% endif %}
  {% if row.watchlist_performance_json %}
    <h2 class="mt-4 font-semibold">Watchlist</h2>
    <pre class="text-xs bg-slate-50 p-2 rounded">{{ row.watchlist_performance_json }}</pre>
  {% endif %}
</section>
{% endblock %}
```

`marketpulse/web/templates/recaps.html`:
```html
{% extends "base.html" %}
{% block content %}
<section class="bg-white rounded-md shadow-sm p-4">
  <h1 class="font-semibold mb-3">Recaps</h1>
  <ul class="text-sm divide-y">
    {% for r in rows %}
    <li class="py-2 flex justify-between">
      <a href="/recap/{{ r.recap_date }}" class="text-blue-600">{{ r.recap_date }}</a>
      <span class="text-slate-500">{{ r.generation_status }}</span>
    </li>
    {% endfor %}
  </ul>
</section>
{% endblock %}
```

- [ ] **Step 4: Wire router**

Modify `marketpulse/web/main.py` to include `recap.router`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/web/test_recap.py -v`
Expected: 2 PASS.

- [ ] **Step 6: Run full test suite as a checkpoint**

Run: `uv run pytest -v`
Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add marketpulse/web tests/web/test_recap.py
git commit -m "feat(web): recap detail + list + retry routes"
```

---

## Phase 7 — Scheduler

### Task 26: APScheduler with daily-recap job

**Files:**
- Create: `marketpulse/scheduler/__init__.py` (empty)
- Create: `marketpulse/scheduler/jobs.py`
- Modify: `marketpulse/web/main.py` (start scheduler on app startup)
- Create: `tests/unit/test_scheduler.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_scheduler.py`:
```python
from datetime import time

from marketpulse.scheduler.jobs import parse_recap_time


def test_parse_recap_time_valid() -> None:
    assert parse_recap_time("16:30") == time(16, 30)


def test_parse_recap_time_invalid_falls_back() -> None:
    assert parse_recap_time("nope") == time(16, 30)
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/unit/test_scheduler.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `marketpulse/scheduler/jobs.py`**

```python
from datetime import date, time, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from marketpulse.ai.client import AnthropicClient
from marketpulse.ai.service import AiService
from marketpulse.config import get_settings
from marketpulse.data.cache import NewsCache
from marketpulse.data.service import DataService
from marketpulse.data.yfinance_client import YFinanceClient
from marketpulse.db.base import session_scope
from marketpulse.logging import get_logger
from marketpulse.recap.service import RecapService

log = get_logger(__name__)


def parse_recap_time(value: str) -> time:
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except Exception:
        return time(16, 30)


def run_daily_recap() -> None:
    target = date.today()
    log.info("recap_job_start", date=str(target))
    settings = get_settings()
    gen = session_scope()
    db = next(gen)
    try:
        data = DataService(db, YFinanceClient())
        ai = AiService(
            db, ai_client=AnthropicClient(), data=data,
            model=settings.ai_model, ttl_hours=settings.ai_cache_ttl_hours,
        )
        svc = RecapService(db, data=data, ai=ai)
        result = svc.generate(target)
        log.info("recap_job_done", date=str(target), status=result.generation_status)
    finally:
        db.close()


def run_news_purge() -> None:
    log.info("news_purge_start")
    gen = session_scope()
    db = next(gen)
    try:
        NewsCache(db).purge_expired()
    finally:
        db.close()


def build_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    sched = BackgroundScheduler(timezone="America/New_York")
    t = parse_recap_time(settings.watchlist_recap_time)
    sched.add_job(
        run_daily_recap,
        trigger=CronTrigger(hour=t.hour, minute=t.minute, day_of_week="mon-fri"),
        id="daily_recap", replace_existing=True, misfire_grace_time=600,
    )
    # 30-minute retry once if previous run failed (the recap service is idempotent)
    sched.add_job(
        run_daily_recap,
        trigger=CronTrigger(hour=t.hour, minute=(t.minute + 30) % 60,
                            day_of_week="mon-fri"),
        id="daily_recap_retry", replace_existing=True, misfire_grace_time=600,
    )
    sched.add_job(
        run_news_purge,
        trigger=CronTrigger(day_of_week="sun", hour=3),
        id="news_purge", replace_existing=True,
    )
    return sched
```

- [ ] **Step 4: Run unit test to verify pass**

Run: `uv run pytest tests/unit/test_scheduler.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Wire scheduler startup/shutdown in `marketpulse/web/main.py`**

In `create_app()`, before `return app`:
```python
    from marketpulse.scheduler.jobs import build_scheduler
    scheduler = build_scheduler()

    @app.on_event("startup")
    def _start_scheduler() -> None:
        if not scheduler.running:
            scheduler.start()

    @app.on_event("shutdown")
    def _stop_scheduler() -> None:
        if scheduler.running:
            scheduler.shutdown(wait=False)
```

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS. (Tests use `TestClient` which triggers startup/shutdown — scheduler must start cleanly without DB writes.)

- [ ] **Step 7: Commit**

```bash
git add marketpulse/scheduler marketpulse/web/main.py tests/unit/test_scheduler.py
git commit -m "feat(scheduler): APScheduler with daily recap + retry + news purge"
```

---

## Phase 8 — Frontend Build & Smoke Test

### Task 27: Tailwind build pipeline

**Files:**
- Create: `package.json`
- Create: `tailwind.config.js`
- Create: `marketpulse/web/static/app.src.css`
- Modify: `.gitignore` (already excludes `app.css`)

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "marketpulse-frontend",
  "private": true,
  "scripts": {
    "build:css": "tailwindcss -i marketpulse/web/static/app.src.css -o marketpulse/web/static/app.css --minify",
    "watch:css": "tailwindcss -i marketpulse/web/static/app.src.css -o marketpulse/web/static/app.css --watch"
  },
  "devDependencies": {
    "tailwindcss": "^3.4.14",
    "@tailwindcss/typography": "^0.5.15"
  }
}
```

- [ ] **Step 2: Create `tailwind.config.js`**

```js
module.exports = {
  content: ["./marketpulse/web/templates/**/*.html"],
  theme: { extend: {} },
  plugins: [require("@tailwindcss/typography")],
};
```

- [ ] **Step 3: Create `marketpulse/web/static/app.src.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 4: Build CSS**

Run: `npm install && npm run build:css`
Expected: writes `marketpulse/web/static/app.css`.

- [ ] **Step 5: Commit (CSS output is gitignored)**

```bash
git add package.json tailwind.config.js marketpulse/web/static/app.src.css
git commit -m "build(web): tailwind pipeline"
```

---

### Task 28: Smoke test script

**Files:**
- Create: `scripts/__init__.py` (empty)
- Create: `scripts/smoke_test.py`

- [ ] **Step 1: Implement `scripts/smoke_test.py`**

```python
"""Manual smoke test against real yfinance + Anthropic.

Run: uv run python scripts/smoke_test.py
"""
from __future__ import annotations

import os
import sys

from marketpulse.config import get_settings
from marketpulse.data.yfinance_client import YFinanceClient


def main() -> int:
    settings = get_settings()
    yf = YFinanceClient()
    print("Fetching AAPL quote …")
    q = yf.fetch_quote("AAPL")
    print(f"  price={q.price:.2f} change={q.change_pct:+.2f}%")
    print("Fetching market overview …")
    m = yf.fetch_market_overview()
    print(f"  SPY={m.spy.change_pct:+.2f}% QQQ={m.qqq.change_pct:+.2f}% VIX={m.vix.price:.2f}")
    if "--with-ai" in sys.argv:
        from marketpulse.ai.client import AnthropicClient
        ai = AnthropicClient()
        out = ai.complete(system="You are concise.", user="Say 'ok'.")
        print(f"  AI replied: {out!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify script imports cleanly**

Run: `uv run python -c "import scripts.smoke_test; print('ok')"`
Expected: prints `ok`.

(We deliberately do NOT execute the real yfinance/Anthropic calls in CI — this script is for manual validation.)

- [ ] **Step 3: Commit**

```bash
git add scripts
git commit -m "chore: smoke-test script for manual external-API validation"
```

---

## Phase 9 — Deployment

### Task 29: Dockerfile

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

- [ ] **Step 1: Create `.dockerignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.mypy_cache/
node_modules/
*.db
.git/
tests/
```

- [ ] **Step 2: Create `Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1.7

FROM node:20-alpine AS css
WORKDIR /app
COPY package.json tailwind.config.js ./
COPY marketpulse/web/static/app.src.css ./marketpulse/web/static/app.src.css
COPY marketpulse/web/templates ./marketpulse/web/templates
RUN npm install && \
    npx tailwindcss \
      -i marketpulse/web/static/app.src.css \
      -o marketpulse/web/static/app.css \
      --minify

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/root/.local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY marketpulse ./marketpulse
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts
COPY --from=css /app/marketpulse/web/static/app.css ./marketpulse/web/static/app.css

RUN useradd -u 1001 -m app && chown -R app /app
USER app

ENV DATABASE_URL=sqlite:////data/marketpulse.db
EXPOSE 8000
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn marketpulse.web.main:app --host 0.0.0.0 --port 8000"]
```

- [ ] **Step 3: Local sanity build**

Run: `docker build -t marketpulse:dev .` (skip if Docker isn't available locally)
Expected: builds successfully.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "build: multi-stage Dockerfile (tailwind + uv runtime)"
```

---

### Task 30: Fly.io configuration

**Files:**
- Create: `fly.toml`
- Create: `docs/DEPLOY.md`

- [ ] **Step 1: Create `fly.toml`**

```toml
app = "marketpulse"
primary_region = "iad"

[build]

[env]
  DATABASE_URL = "sqlite:////data/marketpulse.db"
  LOG_LEVEL = "INFO"
  WATCHLIST_RECAP_TIME = "16:30"

[[mounts]]
  source = "marketpulse_data"
  destination = "/data"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = false
  auto_start_machines = true
  min_machines_running = 1
  [[http_service.checks]]
    interval = "30s"
    timeout = "5s"
    grace_period = "10s"
    method = "GET"
    path = "/health"

[[vm]]
  size = "shared-cpu-1x"
  memory = "256mb"
```

- [ ] **Step 2: Create `docs/DEPLOY.md`**

```markdown
# Deploying MarketPulse to Fly.io

## One-time setup

1. `fly launch --no-deploy` (accept generated `fly.toml` overrides if any).
2. Create the volume:
   `fly volumes create marketpulse_data --region iad --size 1`
3. Set secrets:
   ```
   fly secrets set \
     APP_PASSWORD_HASH="$(uv run python -c 'from marketpulse.auth.password import hash_password; import getpass; print(hash_password(getpass.getpass()))')" \
     SESSION_SECRET="$(openssl rand -hex 32)" \
     ANTHROPIC_API_KEY=sk-ant-...
   ```

## Deploy

```
fly deploy
```

The container runs `alembic upgrade head` on startup, then `uvicorn`.

## Logs / debugging

```
fly logs
fly ssh console
```

## Backup

```
fly ssh console -C "sqlite3 /data/marketpulse.db .dump" > backup-$(date +%Y%m%d).sql
```
```

- [ ] **Step 3: Commit**

```bash
git add fly.toml docs/DEPLOY.md
git commit -m "deploy: fly.io configuration and deploy guide"
```

---

## Phase 10 — Final Verification

### Task 31: End-to-end verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS, no warnings about missing fixtures.

- [ ] **Step 2: Run linter**

Run: `uv run ruff check marketpulse tests`
Expected: no violations. Fix any reported issues.

- [ ] **Step 3: Apply migrations to a fresh local DB**

Run: `rm -f marketpulse.db && uv run alembic upgrade head`
Expected: prints `Running upgrade  -> 0001`.

- [ ] **Step 4: Boot the app locally**

In one terminal:
```bash
export APP_PASSWORD_HASH="$(uv run python -c 'from marketpulse.auth.password import hash_password; print(hash_password("dev"))')"
export SESSION_SECRET="$(openssl rand -hex 32)"
export ANTHROPIC_API_KEY=sk-ant-...
uv run uvicorn marketpulse.web.main:app --reload
```

- [ ] **Step 5: Smoke-test the UI**

Open http://localhost:8000, log in with `dev`, add `AAPL` and `NVDA` to the watchlist, open `/stock/AAPL`, click "AI Deep Analysis", confirm a markdown response renders. Trigger `/recap/<today>/retry` to manually generate a recap and verify it appears on the home page.

- [ ] **Step 6: Run the manual smoke-test script**

Run: `uv run python scripts/smoke_test.py --with-ai`
Expected: prints quote, market overview, AI reply.

- [ ] **Step 7: Final commit if verification surfaced any fixes**

```bash
git status
# only commit if there are changes
git add -A
git commit -m "chore: end-to-end verification fixes"
```

- [ ] **Step 8: Tag v0.1.0**

```bash
git tag -a v0.1.0 -m "MarketPulse v1 MVP"
```

---

## Spec Coverage Checklist

- ✅ Watchlist CRUD — Task 22
- ✅ Daily recap (market + per-ticker perf + signals + news + AI commentary) — Tasks 15, 16, 26
- ✅ On-demand AI deep analysis — Tasks 12, 14, 24
- ✅ Historical recap browsing — Task 25
- ✅ Single-user password auth — Tasks 17, 18, 20
- ✅ FastAPI + HTMX + SQLite + APScheduler stack — Tasks 4, 19, 26
- ✅ yfinance only in `data/yfinance_client.py` — Task 8
- ✅ Anthropic only in `ai/client.py` — Task 13
- ✅ Cache-first reads with stale fallback — Task 11
- ✅ Prompt versioning invalidates AI cache — Task 14
- ✅ Recap idempotency + partial-failure handling — Task 16
- ✅ Visible-and-recoverable errors — Tasks 16, 21
- ✅ Structured JSON logging — Task 3
- ✅ Health check — Task 19
- ✅ Alembic migrations — Task 6
- ✅ Dockerfile + fly.toml — Tasks 29, 30
- ✅ Manual smoke-test script — Task 28
