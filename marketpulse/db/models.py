from datetime import UTC, date, datetime
from decimal import Decimal as _Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    sector: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # Source of this dividend: "manual" | "tencent" | "yfinance" | "import".
    # Lets reconciliation prefer one over another and helps debug data origin.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_dividends_ticker_ex_date", "ticker", "ex_date"),
        UniqueConstraint("ticker", "ex_date", name="uq_dividends_ticker_date"),
        CheckConstraint(
            "amount_per_share >= 0 AND total_amount >= 0",
            name="ck_dividends_amounts_non_negative",
        ),
    )


class StockSplit(Base):
    """Corporate-action split event. Preserves original Trade rows; the
    splits-aware recompute applies these in chronological order to derive
    current Holding state. See docs/superpowers/specs/2026-05-11-stock-splits-design.md.
    """
    __tablename__ = "stock_splits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    # new_shares / old_shares. Forward 1:2 = 2.0; reverse 5:1 = 0.2.
    # CHECK constraint at the DB level guards against bad data even if a
    # caller bypasses service-layer validation.
    ratio: Mapped[float] = mapped_column(Float, nullable=False)
    # "yfinance" | "manual" | "import" — lets reconciliation prefer one over another.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("ticker", "ex_date", name="uq_stock_splits_ticker_date"),
        CheckConstraint("ratio > 0 AND ratio != 1", name="ck_stock_splits_ratio_valid"),
        Index("ix_stock_splits_ticker_ex_date", "ticker", "ex_date"),
    )


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
    key_events_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    generation_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class AiAnalysis(Base):
    __tablename__ = "ai_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
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


class EvaluationEvent(Base):
    """A point-in-time event we want to evaluate later.

    event_type partitions: "ai_analysis" | "signal_marker"
    subtype values come from marketpulse.evaluation.constants
    """
    __tablename__ = "evaluation_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subtype: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    event_price: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC),
    )

    outcomes: Mapped[list["EvaluationOutcome"]] = relationship(
        back_populates="event", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_event_lookup", "event_type", "subtype", "ticker", "event_time"),
    )


class EvaluationOutcome(Base):
    """Forward-return measurement at a given horizon for an event."""
    __tablename__ = "evaluation_outcome"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_event.id"), nullable=False, index=True,
    )
    horizon_trading_days: Mapped[int] = mapped_column(Integer, nullable=False)
    event_price: Mapped[float] = mapped_column(Float, nullable=False)
    horizon_price: Mapped[float] = mapped_column(Float, nullable=False)
    horizon_date: Mapped[date] = mapped_column(Date, nullable=False)
    forward_return: Mapped[float] = mapped_column(Float, nullable=False)
    benchmark_ticker: Mapped[str] = mapped_column(String(16), default="SPY")
    benchmark_forward_return: Mapped[float] = mapped_column(Float, nullable=False)
    excess_return: Mapped[float] = mapped_column(Float, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC),
    )

    event: Mapped["EvaluationEvent"] = relationship(back_populates="outcomes")

    __table_args__ = (
        UniqueConstraint("event_id", "horizon_trading_days",
                         name="uq_event_horizon"),
    )


# === Phase 6a paper-trading models ===
# Lock xv: NO modifications to existing Phase 1-5 tables.
# Lock xxii: Decimal(18, 6) for all price/cash/P&L columns.
# Lock xxix: All timestamps UTC via TZDateTime TypeDecorator.
# Lock xiii: paper_fill, paper_audit_event, paper_cash_ledger are append-only.


class PaperOrder(Base):
    __tablename__ = "paper_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    allocation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    event_time: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    allocation_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_date: Mapped[date] = mapped_column(Date, nullable=False)
    placed_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    filled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    event_price: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    horizon_price: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False)

    strategy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    allocator_version: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_engine_version: Mapped[str] = mapped_column(String(32), nullable=False)

    # Phase 5 allocation provenance
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    raw_bid_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    pool_corr: Mapped[float | None] = mapped_column(Float, nullable=True)
    contribution_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    adjusted_bid_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_corr_window: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    rewarded_for_negative_corr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    would_change_rank: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    size_clamped_by_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_paper_order_status_horizon", "status", "horizon_date"),
        Index("ix_paper_order_status_alloc_date", "status", "allocation_date"),
        Index("ix_paper_order_alloc_date_strategy", "allocation_date", "strategy"),
        Index("ix_paper_order_strategy_placed", "strategy", "placed_at"),
        Index("ix_paper_order_run_id", "allocation_run_id"),
        CheckConstraint(
            "status IN ('PLACED', 'ENTRY_FILLED', 'CANCELLED')",
            name="ck_paper_order_status",
        ),
        CheckConstraint("quantity > 0", name="ck_paper_order_qty_positive"),
        # Time-consistency CHECKs (spec § 4.1 — round-7 merge):
        CheckConstraint(
            "status != 'PLACED' OR (filled_at IS NULL AND cancelled_at IS NULL)",
            name="ck_paper_order_placed_no_terminal_ts",
        ),
        CheckConstraint(
            "status != 'ENTRY_FILLED' OR filled_at IS NOT NULL",
            name="ck_paper_order_entry_filled_has_ts",
        ),
        CheckConstraint(
            "status != 'CANCELLED' OR cancelled_at IS NOT NULL",
            name="ck_paper_order_cancelled_has_ts",
        ),
    )


class PaperFill(Base):
    __tablename__ = "paper_fill"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("paper_order.id"), nullable=False)
    # FK is safe in this direction: paper_position is created first (with
    # entry_fill_id NULL), THEN paper_fill INSERT references the known
    # position_id. The circular-FK problem only affects the reverse
    # direction (paper_position.entry_fill_id / exit_fill_id → paper_fill),
    # which is why THOSE two columns stay as plain nullable Integer.
    position_id: Mapped[int] = mapped_column(ForeignKey("paper_position.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    cash_delta: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    realized_pnl: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    __table_args__ = (
        Index("ix_paper_fill_order_id", "order_id"),
        Index("ix_paper_fill_position_side", "position_id", "side"),
        UniqueConstraint("order_id", "side", name="uq_paper_fill_order_side"),
        CheckConstraint("side IN ('ENTRY', 'EXIT')", name="ck_paper_fill_side"),
        CheckConstraint("quantity > 0", name="ck_paper_fill_qty_positive"),
    )


class PaperPosition(Base):
    __tablename__ = "paper_position"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("paper_order.id"), nullable=False, unique=True)
    # entry_fill_id / exit_fill_id: per spec § 4.7, plain nullable INTEGER on SQLite v0
    # (no FK to paper_fill to avoid the circular-FK problem during ENTRY-flow
    # transaction). Phase 7 / Postgres migration tightens to deferred FKs.
    entry_fill_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_fill_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    exit_price: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    realized_pnl: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    __table_args__ = (
        Index("ix_paper_position_status_horizon", "status", "horizon_date"),
        Index("ix_paper_position_strategy_ticker", "strategy", "ticker"),
        Index("ix_paper_position_entry_fill", "entry_fill_id"),
        Index("ix_paper_position_exit_fill", "exit_fill_id"),
        CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_paper_position_status"),
        CheckConstraint(
            "status != 'OPEN' OR exit_fill_id IS NULL",
            name="ck_paper_position_open_no_exit",
        ),
        CheckConstraint(
            "status != 'CLOSED' OR (entry_fill_id IS NOT NULL AND exit_fill_id IS NOT NULL)",
            name="ck_paper_position_closed_both_set",
        ),
    )


class PaperCashLedger(Base):
    __tablename__ = "paper_cash_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    delta: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    fill_id: Mapped[int | None] = mapped_column(ForeignKey("paper_fill.id"), nullable=True)
    balance_after: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)

    __table_args__ = (
        Index("ix_paper_cash_ts", "timestamp"),
        Index("ix_paper_cash_fill", "fill_id"),
        CheckConstraint(
            "reason IN ('ENTRY_FILL', 'EXIT_FILL', 'INITIAL_DEPOSIT', 'MANUAL_ADJUSTMENT')",
            name="ck_paper_cash_reason",
        ),
    )


class PaperAuditEvent(Base):
    __tablename__ = "paper_audit_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_paper_audit_ts", "timestamp"),
        Index("ix_paper_audit_type_ts", "event_type", "timestamp"),
        Index("ix_paper_audit_order", "order_id"),
        Index("ix_paper_audit_strategy_ts", "strategy", "timestamp"),
        CheckConstraint(
            "event_type IN ("
            "'ORDER_PLACED', 'ORDER_PLACED_DUPLICATE', 'ORDER_REJECTED', "
            "'ORDER_CANCELLED', 'ORDER_ENTRY_FILLED', 'POSITION_CLOSED', "
            "'KILL_SWITCH_FLIPPED', 'KILL_SWITCH_CYCLE_SKIPPED', "
            "'TICK_COMPLETED', 'TICK_REPROCESSED_COMPLETED', "
            "'SCHEDULER_GAP_DETECTED', 'ENGINE_INVARIANT_ERROR', "
            "'PRICE_UNAVAILABLE'"
            ")",
            name="ck_paper_audit_event_type",
        ),
    )
