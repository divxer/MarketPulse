"""price_cache is_final

Revision ID: f43156b7577f
Revises: 83cf7ac9e055
"""
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from alembic import op

revision: str = 'f43156b7577f'
down_revision: str | Sequence[str] | None = '83cf7ac9e055'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Finality rule INLINED (not imported from app code) so the migration stays
# frozen even if marketpulse.data.finality evolves. Spec §2: final iff
# fetched_at >= 16:05 America/New_York on the bar's own date.
_NY = ZoneInfo("America/New_York")
_CUTOFF = time(16, 5)


def _is_final(bar_date: date, fetched_at: datetime) -> bool:
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    cutoff_utc = datetime.combine(bar_date, _CUTOFF, tzinfo=_NY).astimezone(UTC)
    return fetched_at.astimezone(UTC) >= cutoff_utc


def upgrade() -> None:
    with op.batch_alter_table("price_cache") as batch:
        batch.add_column(sa.Column(
            "is_final", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))
        batch.add_column(sa.Column("finalized_at", sa.DateTime(), nullable=True))

    # Python backfill — the cutoff is an NY wall-clock rule; UTC offsets shift
    # with DST, so this cannot be a single SQL expression.
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT ticker, date, fetched_at FROM price_cache",
    )).fetchall()
    final_keys = []
    for ticker, bar_date_s, fetched_at_s in rows:
        bar_date = date.fromisoformat(str(bar_date_s))
        fetched_at = datetime.fromisoformat(str(fetched_at_s))
        if _is_final(bar_date, fetched_at):
            final_keys.append({"t": ticker, "d": str(bar_date_s), "f": str(fetched_at_s)})
    if final_keys:
        bind.execute(
            sa.text(
                "UPDATE price_cache SET is_final = 1, finalized_at = :f "
                "WHERE ticker = :t AND date = :d",
            ),
            final_keys,
        )
    # P1 review: the migration must report its own stats — deploy verification
    # should not depend on a separately-run analysis query.
    print(
        f"price_cache is_final backfill: total={len(rows)} "
        f"final={len(final_keys)} provisional={len(rows) - len(final_keys)}",
    )


def downgrade() -> None:
    with op.batch_alter_table("price_cache") as batch:
        batch.drop_column("finalized_at")
        batch.drop_column("is_final")
