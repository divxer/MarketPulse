from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column

from marketpulse.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TZDateTime(TypeDecorator[datetime]):
    """DateTime column that always returns a UTC-aware datetime on read.

    SQLite stores datetimes as ISO strings without timezone semantics; SQLAlchemy
    returns naive datetimes by default. This decorator ensures every read attaches
    UTC, so downstream comparisons against `datetime.now(UTC)` never raise TypeError.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    added_at: Mapped[datetime] = mapped_column(TZDateTime(), default=_utcnow, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Holding(Base):
    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    avg_cost: Mapped[float] = mapped_column(Float, nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=_utcnow, nullable=False)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False)  # 'buy' or 'sell'
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    fees: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    realized_pl: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=_utcnow, nullable=False)

    __table_args__ = (Index("ix_trades_ticker_created", "ticker", "created_at"),)


class Dividend(Base):
    """Cash dividend received on a held position. Separate from Trade because
    dividends don't change share count or cost basis — they're income only.
    """
    __tablename__ = "dividends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Per-share payout; total = per_share * shares held at record date.
    # Both are stored explicitly so we can round-trip the original 腾讯自选股 entry.
    amount_per_share: Mapped[float] = mapped_column(Float, nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=_utcnow, nullable=False)

    __table_args__ = (Index("ix_dividends_ticker_ex_date", "ticker", "ex_date"),)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    # metric ∈ {'price', 'change_pct', 'volume_ratio'}; op ∈ {'>=', '<='}
    metric: Mapped[str] = mapped_column(String(16), nullable=False)
    op: Mapped[str] = mapped_column(String(2), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=_utcnow, nullable=False)

    __table_args__ = (Index("ix_alert_rules_enabled", "enabled"),)


class DailyRecap(Base):
    __tablename__ = "daily_recaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recap_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    market_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    watchlist_performance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    holdings_overview_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    holdings_totals_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    news_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_commentary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
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
    requested_at: Mapped[datetime] = mapped_column(TZDateTime(), default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)

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
    fetched_at: Mapped[datetime] = mapped_column(TZDateTime(), default=_utcnow, nullable=False)


class NewsCacheEntry(Base):
    __tablename__ = "news_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_news_cache_ticker_published_at", "ticker", "published_at"),
        UniqueConstraint("ticker", "url", name="uq_news_ticker_url"),
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
