# Layer: db
"""SQLAlchemy repository for paper_nav_snapshot.

L1: normal flow is INSERT only (insert_snapshot). The admin path is
force_replace_snapshot(reason). L20: unpriced_tickers is stored as
comma-separated TEXT; None/"" parse to empty tuple.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketpulse.db.models import PaperNavSnapshot
from marketpulse.portfolio.north_star import NavSnapshot


class SnapshotAlreadyExists(Exception):
    """Raised by insert_snapshot on PK conflict. Use force_replace_snapshot
    for the admin/rebuild path."""


def _encode_tickers(tickers: tuple[str, ...]) -> str | None:
    """L20: empty tuple → None; otherwise comma-join sorted unique tickers."""
    if not tickers:
        return None
    # L20 invariant: tickers MUST NOT contain commas (the on-disk encoding
    # uses comma as the separator). Existing ingestion enforces ticker
    # grammar; this assertion makes the invariant self-enforcing at the
    # storage boundary.
    if any("," in t for t in tickers):
        raise ValueError(
            f"L20 violation: ticker contains comma — {tickers!r}"
        )
    return ",".join(sorted(set(tickers)))


def _decode_tickers(raw: str | None) -> tuple[str, ...]:
    """L20: None/"" → empty tuple."""
    if not raw:
        return ()
    return tuple(raw.split(","))


def _row_to_dc(row: PaperNavSnapshot) -> NavSnapshot:
    return NavSnapshot(
        trading_date=row.trading_date,
        cash_balance=row.cash_balance,
        holdings_mtm=row.holdings_mtm,
        portfolio_nav=row.portfolio_nav,
        anchor_portfolio_nav=row.anchor_portfolio_nav,
        portfolio_index=row.portfolio_index,
        spy_close=row.spy_close,
        anchor_spy_close=row.anchor_spy_close,
        spy_index=row.spy_index,
        excess_return=row.excess_return,
        trading_days_observed=row.trading_days_observed,
        coverage_ratio=row.coverage_ratio,
        is_sufficient=row.is_sufficient,
        unpriced_positions_count=row.unpriced_positions_count,
        unpriced_tickers=_decode_tickers(row.unpriced_tickers),
    )


def _dc_to_kwargs(snap: NavSnapshot, *, now: datetime) -> dict:
    return dict(
        trading_date=snap.trading_date,
        cash_balance=snap.cash_balance,
        holdings_mtm=snap.holdings_mtm,
        portfolio_nav=snap.portfolio_nav,
        anchor_portfolio_nav=snap.anchor_portfolio_nav,
        portfolio_index=snap.portfolio_index,
        spy_close=snap.spy_close,
        anchor_spy_close=snap.anchor_spy_close,
        spy_index=snap.spy_index,
        excess_return=snap.excess_return,
        trading_days_observed=snap.trading_days_observed,
        coverage_ratio=snap.coverage_ratio,
        is_sufficient=snap.is_sufficient,
        unpriced_positions_count=snap.unpriced_positions_count,
        unpriced_tickers=_encode_tickers(snap.unpriced_tickers),
        created_at=now,
        updated_at=now,
        is_rebuilt=False,
        rebuild_reason=None,
    )


def insert_snapshot(session: Session, snapshot: NavSnapshot) -> None:
    """Insert exactly once. Raises SnapshotAlreadyExists on PK conflict.

    IMPORTANT: this function does NOT call session.rollback(). Repository
    functions must not control transaction state — that's the caller's
    responsibility. The runner pre-checks existence before computing, so
    in normal flow the race-to-flush path is unreachable; if it does fire
    (concurrent writer), the caller decides whether to rollback or retry.
    """
    existing = session.get(PaperNavSnapshot, snapshot.trading_date)
    if existing is not None:
        raise SnapshotAlreadyExists(
            f"snapshot already exists for {snapshot.trading_date}"
        )
    row = PaperNavSnapshot(**_dc_to_kwargs(snapshot, now=datetime.now(UTC)))
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        # Caller decides rollback policy.
        raise SnapshotAlreadyExists(
            f"snapshot already exists for {snapshot.trading_date}"
        ) from exc


def force_replace_snapshot(
    session: Session, snapshot: NavSnapshot, *, reason: str,
) -> None:
    """Admin/rebuild path. Sets is_rebuilt=True and rebuild_reason.
    Preserves created_at; sets updated_at = now."""
    now = datetime.now(UTC)
    row = session.get(PaperNavSnapshot, snapshot.trading_date)
    if row is None:
        # No prior row — straight insert with rebuild flags set.
        kwargs = _dc_to_kwargs(snapshot, now=now)
        kwargs["is_rebuilt"] = True
        kwargs["rebuild_reason"] = reason
        session.add(PaperNavSnapshot(**kwargs))
        session.flush()
        return

    # Mutate the existing row in place; preserve created_at.
    new_kwargs = _dc_to_kwargs(snapshot, now=now)
    for key in (
        "cash_balance", "holdings_mtm", "portfolio_nav",
        "anchor_portfolio_nav", "portfolio_index",
        "spy_close", "anchor_spy_close", "spy_index", "excess_return",
        "trading_days_observed", "coverage_ratio", "is_sufficient",
        "unpriced_positions_count", "unpriced_tickers",
    ):
        setattr(row, key, new_kwargs[key])
    row.updated_at = now
    row.is_rebuilt = True
    row.rebuild_reason = reason
    session.flush()


def get_snapshot(session: Session, trading_date: date) -> NavSnapshot | None:
    row = session.get(PaperNavSnapshot, trading_date)
    return _row_to_dc(row) if row is not None else None


def get_latest_snapshot(session: Session) -> NavSnapshot | None:
    row = session.scalars(
        select(PaperNavSnapshot)
        .order_by(PaperNavSnapshot.trading_date.desc())
        .limit(1),
    ).first()
    return _row_to_dc(row) if row is not None else None


def get_snapshot_series(
    session: Session, *, window_start: date, window_end: date,
) -> list[NavSnapshot]:
    """Inclusive range, ordered by trading_date ascending."""
    rows = session.scalars(
        select(PaperNavSnapshot)
        .where(PaperNavSnapshot.trading_date >= window_start)
        .where(PaperNavSnapshot.trading_date <= window_end)
        .order_by(PaperNavSnapshot.trading_date.asc()),
    ).all()
    return [_row_to_dc(r) for r in rows]


def get_recent_snapshot_dates(
    session: Session, *, limit: int,
) -> list[date]:
    """Most-recent N trading_dates, returned in ASCENDING order."""
    desc_dates = list(session.scalars(
        select(PaperNavSnapshot.trading_date)
        .order_by(PaperNavSnapshot.trading_date.desc())
        .limit(limit),
    ).all())
    return sorted(desc_dates)


def count_snapshots_in_window(
    session: Session, *, window_end: date, window_size: int,
) -> int:
    """L11: count of most-recent snapshot rows with trading_date <= window_end,
    capped at window_size. Trading-day semantics — NEVER calendar:
    if 200 snapshots exist and window_size=90, returns 90, regardless of
    calendar gap from earliest snapshot to window_end."""
    total = session.scalar(
        select(func.count(PaperNavSnapshot.trading_date))
        .where(PaperNavSnapshot.trading_date <= window_end),
    )
    return min(int(total or 0), window_size)


def get_spy_anchor(session: Session) -> Decimal | None:
    """L16: earliest non-null anchor_spy_close in the snapshot table."""
    return session.scalar(
        select(PaperNavSnapshot.anchor_spy_close)
        .where(PaperNavSnapshot.anchor_spy_close.is_not(None))
        .order_by(PaperNavSnapshot.trading_date.asc())
        .limit(1),
    )


def get_earliest_snapshot(session: Session) -> NavSnapshot | None:
    """Used by snapshot_runner to recover anchor_portfolio_nav on every
    subsequent snapshot after the first."""
    row = session.scalars(
        select(PaperNavSnapshot)
        .order_by(PaperNavSnapshot.trading_date.asc())
        .limit(1),
    ).first()
    return _row_to_dc(row) if row is not None else None


def get_earliest_eligible_snapshot(session: Session) -> NavSnapshot | None:
    """Earliest snapshot fit to be the north-star inception anchor: fully
    priced (unpriced_positions_count == 0) AND benchmarked (spy_close present).

    Degenerate rows — produced by pre-market manual triggers, price_cache gaps,
    or transient SPY/position-price absence — are skipped so they never become
    the inception baseline (which would inflate portfolio_index for the whole
    series). Anchoring BOTH indices to one eligible day also keeps
    excess_return = portfolio_index - spy_index coherent (shared t=0)."""
    row = session.scalars(
        select(PaperNavSnapshot)
        .where(PaperNavSnapshot.spy_close.is_not(None))
        .where(PaperNavSnapshot.unpriced_positions_count == 0)
        .order_by(PaperNavSnapshot.trading_date.asc())
        .limit(1),
    ).first()
    return _row_to_dc(row) if row is not None else None


def get_all_snapshots(session: Session) -> list[NavSnapshot]:
    """All snapshots, ascending by trading_date.

    Read-only UI helper for /lab/portfolio-vs-spy (L12). NOT used by snapshot
    computation or anchor-recovery paths.
    """
    rows = session.scalars(
        select(PaperNavSnapshot)
        .order_by(PaperNavSnapshot.trading_date.asc()),
    ).all()
    return [_row_to_dc(r) for r in rows]
