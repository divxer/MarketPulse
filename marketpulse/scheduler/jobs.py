from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from marketpulse.ai.client import AnthropicClient
from marketpulse.ai.service import AiService
from marketpulse.alerts.engine import evaluate_rules
from marketpulse.alerts.notifier import NoopNotifier, build_notifier
from marketpulse.config import get_settings
from marketpulse.data.cache import NewsCache
from marketpulse.data.hybrid_client import HybridClient
from marketpulse.data.service import DataService
from marketpulse.data.tencent_client import CorporateActions, TencentClient
from marketpulse.data.yfinance_client import YFinanceClient
from marketpulse.db.base import session_scope
from marketpulse.db.models import Holding, WatchlistItem
from marketpulse.holdings.dividends import DividendError, record_dividend
from marketpulse.holdings.quantity_history import quantity_as_of
from marketpulse.holdings.splits import SplitError, record_split
from marketpulse.holdings.trades import recompute_ticker
from marketpulse.logging import get_logger
from marketpulse.ops.charter_review import generate_charter_review
from marketpulse.portfolio.snapshot_runner import run_nav_snapshot
from marketpulse.recap.push import push_recap_summary
from marketpulse.recap.service import RecapService
from marketpulse.scheduler.state import record_run_summary

log = get_logger(__name__)


def _build_quote_client():
    s = get_settings()
    yf = YFinanceClient()
    source = (s.quote_source or "auto").lower()
    if source == "yfinance":
        return yf
    return HybridClient(
        yf, tencent=TencentClient(), prefer_tencent=source in ("auto", "tencent"),
    )


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
        data = DataService(db, _build_quote_client(), news_ttl_days=settings.news_cache_ttl_days)
        ai = AiService(
            db, ai_client=AnthropicClient(), data=data,
            model=settings.ai_model, ttl_hours=settings.ai_cache_ttl_hours,
            model_analyze=settings.ai_model_analyze or None,
            model_router=settings.ai_model_router or None,
        )
        svc = RecapService(db, data=data, ai=ai)
        result = svc.generate(target)
        log.info("recap_job_done", date=str(target), status=result.generation_status)

        # Optional push — non-blocking, never fails the job.
        # Skip if the recap itself failed: an empty body has no value.
        if settings.notifier_recap_enabled and result.generation_status == "success":
            notifier = build_notifier(settings)
            if not isinstance(notifier, NoopNotifier):
                try:
                    push_recap_summary(
                        result, notifier,
                        base_url=settings.public_base_url or None,
                        notifier_kind=settings.notifier_kind,
                    )
                except Exception as exc:
                    log.warning("recap_push_skipped", error=str(exc))
        elif settings.notifier_recap_enabled and result.generation_status != "success":
            log.info("recap_push_skipped_status", status=result.generation_status)
    finally:
        db.close()


def run_alert_check() -> None:
    settings = get_settings()
    if settings.notifier_kind == "none":
        return  # No transport configured; skip the work.
    gen = session_scope()
    db = next(gen)
    try:
        data = DataService(
            db, _build_quote_client(), news_ttl_days=settings.news_cache_ttl_days,
        )
        notifier = build_notifier(settings)
        results = evaluate_rules(
            db, data=data, notifier=notifier,
            debounce_minutes=settings.alert_debounce_minutes,
        )
        fired = sum(1 for r in results if r.get("fired"))
        if fired:
            log.info("alert_check_done", evaluated=len(results), fired=fired)
    finally:
        db.close()


def run_sector_backfill() -> None:
    """Fill Holding.sector for any NULL rows via yfinance .info.

    Previously called inline on every /holdings GET (bounded to 3
    tickers per request), which added ~1-2s × 3 = up to 6s of latency
    on cold cache. Moved to a daily scheduler job — sector data is
    stable enough that "filled within 24h of adding a ticker" is fine,
    and /holdings now renders without any yfinance Ticker.info calls.
    """
    from marketpulse.holdings.sector import backfill_holding_sectors
    log.info("sector_backfill_start")
    gen = session_scope()
    db = next(gen)
    try:
        n = backfill_holding_sectors(db, max_per_call=100)
        log.info("sector_backfill_done", rows_filled=n)
    finally:
        db.close()


def _run_nav_snapshot_safely(session, *, tick_date) -> None:
    """PR3a — EOD NAV snapshot. Piggybacks on tick fill settlement.

    L4: only non-PK persistence errors are caught here; PK conflicts are
    handled INSIDE run_nav_snapshot (idempotent re-run). The tick is
    never aborted by snapshot failure.

    `exception` (not `error`) in the extra dict avoids collision with
    stdlib LogRecord field names and most structured-logging formatters.
    """
    try:
        run_nav_snapshot(session, trading_date=tick_date)
        # The runner only add()+flush()es; session_scope/the tick wrapper do
        # NOT commit on close, so without this commit the snapshot is rolled
        # back when the tick session closes (prod: ticks ran but
        # paper_nav_snapshot stayed empty). Commit so the row persists.
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.warning(
            "nav_snapshot_failed",
            extra={"tick_date": str(tick_date), "exception": str(exc)},
        )


def run_db_backup() -> None:
    """Charter top-3 priority #1: SQLite safety floor.

    Online snapshot of marketpulse.db to /data/backups/, integrity-check
    the snapshot, update latest.json manifest, prune backups older than
    7 days. See docs/superpowers/specs/2026-05-28-db-backup-design.md.

    Never raises — a failed backup writes a status="failed" manifest and
    logs a warning. The scheduler must not crash because of this job.
    """
    from sqlalchemy.engine.url import make_url

    from marketpulse.ops.backup import (
        MANIFEST_FILENAME,
        prune_old_backups,
        run_backup,
        write_manifest,
    )
    settings = get_settings()
    db_url = settings.database_url
    # Parse via SQLAlchemy so absolute (sqlite:////data/x.db) and relative
    # (sqlite:///./x.db) URLs both resolve correctly without slash-counting.
    parsed = make_url(db_url)
    if parsed.drivername != "sqlite" or not parsed.database:
        log.info("db_backup_skipped_not_sqlite", database_url=db_url)
        return
    source = Path(parsed.database).resolve()
    backups_dir = source.parent / "backups"
    log.info("db_backup_start", source=str(source), destination_dir=str(backups_dir))
    result = run_backup(source=source, backups_dir=backups_dir)
    write_manifest(
        manifest_path=backups_dir / MANIFEST_FILENAME, result=result,
    )
    if result.status == "ok":
        pruned = prune_old_backups(backups_dir=backups_dir)
        log.info(
            "db_backup_done",
            destination=result.destination, size_bytes=result.size_bytes,
            duration_ms=result.duration_ms, pruned=len(pruned),
        )
    else:
        log.warning(
            "db_backup_failed",
            error=result.error, duration_ms=result.duration_ms,
        )


def _last_sunday_on_or_before(d: date) -> date:
    """Mon=0..Sun=6. Returns d if Sunday, else d minus (weekday+1) days."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def run_charter_review_weekly() -> None:
    """Mon 09:30 UTC — generate the weekly charter review markdown.

    L4: errors from generate_charter_review are caught here and logged;
    the scheduler must not crash because of this job.
    L13: skipped with info log if database_url isn't a sqlite driver.
    """
    from contextlib import suppress

    from sqlalchemy.engine.url import make_url

    settings = get_settings()
    parsed = make_url(settings.database_url)
    if not parsed.drivername.startswith("sqlite") or not parsed.database:
        log.info(
            "charter_review_skipped_not_sqlite",
            database_url=settings.database_url,
        )
        return
    source_db = Path(parsed.database).resolve()
    data_dir = source_db.parent
    recaps_dir = data_dir / "recaps" / "charter"
    backup_manifest_path = data_dir / "backups" / "latest.json"
    now = datetime.now(UTC)
    week_ending = _last_sunday_on_or_before(now.date())
    try:
        gen = session_scope()
        session = next(gen)
        try:
            generate_charter_review(
                session=session,
                week_ending=week_ending,
                now=now,
                recaps_dir=recaps_dir,
                backup_manifest_path=backup_manifest_path,
            )
        finally:
            with suppress(Exception):
                session.close()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "charter_review_failed",
            extra={"week_ending": str(week_ending), "exception": str(exc)},
        )


def run_news_purge() -> None:
    log.info("news_purge_start")
    settings = get_settings()
    gen = session_scope()
    db = next(gen)
    try:
        NewsCache(db, ttl_days=settings.news_cache_ttl_days).purge_expired()
    finally:
        db.close()


def run_detect_corporate_actions() -> None:
    """Daily 17:00 ET: pull dividends + splits from Tencent for every
    held/watched ticker; fall back to yfinance on Tencent failure.

    Idempotent — duplicate (ticker, ex_date) at the service layer is swallowed.
    Dividends are only recorded when shares were held on ex_date (per
    quantity_as_of). Splits are always recorded so future buys recompute
    correctly. recompute_ticker is called unconditionally per ticker so that
    Holdings stay consistent with the timeline even when no new splits land
    (e.g. after a trade re-import where existing splits should still
    multiply the qty into the Holding row).
    """
    log.info("detect_corporate_actions_start")
    tencent = TencentClient()
    yf_client = YFinanceClient()
    today = date.today()
    since = today - timedelta(days=1825)  # ~5 years lookback

    # Per-ticker accumulator. Populated as the loop runs; persisted to
    # AppSetting in `finally` so a mid-run exception still leaves a useful
    # diagnostic for /health/scheduler.
    results: list[dict[str, object]] = []
    started_at = datetime.now(UTC)

    gen = session_scope()
    db = next(gen)
    try:
        held = [h.ticker for h in db.query(Holding).all()]
        watched = [w.ticker for w in db.query(WatchlistItem).all()]
        seen: set[str] = set()
        tickers: list[str] = []
        for t in held + watched:
            if t not in seen:
                seen.add(t)
                tickers.append(t)

        for t in tickers:
            actions, src = _fetch_corp_actions(t, tencent, yf_client, since, today)
            if actions is None:
                results.append({
                    "ticker": t, "source": "none",
                    "splits_added": 0, "dividends_added": 0,
                    "error": "both sources failed",
                })
                continue  # both sources logged + failed

            splits_added = 0
            dividends_added = 0

            # Splits: record for all tickers (watchlist-only included).
            for ex_date, ratio in actions.splits:
                try:
                    record_split(
                        db, ticker=t, ex_date=ex_date, ratio=ratio, source=src,
                    )
                    log.info("split_recorded", ticker=t,
                             ex_date=str(ex_date), ratio=ratio, source=src)
                    splits_added += 1
                except SplitError:
                    pass  # already recorded

            # Dividends: only record when shares held the day BEFORE ex_date.
            # US dividend entitlement = "holder of record at close of T-1",
            # i.e. you must already own the shares the trading day before
            # ex-date. Selling ON ex-date does not forfeit the dividend;
            # buying ON ex-date does not earn it. Pass `ex_date - 1 day` so
            # same-day trades and same-day splits are correctly excluded
            # from the qty snapshot.
            for ex_date, per_share in actions.dividends:
                qty = quantity_as_of(db, t, ex_date - timedelta(days=1))
                if qty <= 0:
                    continue
                try:
                    record_dividend(
                        db, ticker=t, ex_date=ex_date,
                        amount_per_share=per_share,
                        total_amount=qty * per_share,
                        source=src,
                    )
                    log.info("dividend_recorded", ticker=t,
                             ex_date=str(ex_date), per_share=per_share,
                             qty=qty, source=src)
                    dividends_added += 1
                except DividendError:
                    pass  # already recorded

            # Always recompute, even if no new splits landed. Catches the case
            # where trades were imported after splits already existed in DB —
            # record_trade uses raw arithmetic and would leave the Holding row
            # missing the split multiplication. Cost: one short walk per ticker.
            recompute_ticker(db, t)

            results.append({
                "ticker": t, "source": src,
                "splits_added": splits_added,
                "dividends_added": dividends_added,
                "error": None,
            })
    finally:
        # Best-effort summary write. If even this fails, log it but don't crash
        # the scheduler — the job's actual work was already committed above.
        try:
            record_run_summary(db, {
                "ran_at": started_at.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "tickers": results,
                "total_splits": sum(r["splits_added"] for r in results),
                "total_dividends": sum(r["dividends_added"] for r in results),
                "total_failures": sum(1 for r in results if r["error"]),
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduler_state_write_failed", error=str(exc))
        db.close()
    log.info("detect_corporate_actions_done")


def _fetch_corp_actions(ticker, tencent, yf_client, since, today):
    """Try Tencent first; fall back to yfinance on any exception.
    Returns (CorporateActions, source_label) or (None, "none") on total failure.
    Never raises.
    """
    try:
        actions = tencent.fetch_corporate_actions(ticker, start=since, end=today)
        return actions, "tencent"
    except Exception as exc:  # noqa: BLE001 — best-effort across data sources
        log.warning("tencent_corp_actions_failed",
                    ticker=ticker, error=str(exc))
    try:
        splits = yf_client.fetch_splits(ticker)
        dividends = yf_client.fetch_dividends(ticker)
        return CorporateActions(dividends=dividends, splits=splits), "yfinance"
    except Exception as exc:  # noqa: BLE001
        log.warning("corp_actions_all_sources_failed",
                    ticker=ticker, error=str(exc))
        return None, "none"


def run_outcome_computation() -> None:
    """Daily job: compute outcomes for pending evaluation events.

    Runs at 02:00 UTC. US market close ~21:00 UTC → 5h buffer for yfinance.
    """
    from marketpulse.evaluation import compute_outcomes_for_pending_events

    settings = get_settings()
    gen = session_scope()
    db = next(gen)
    try:
        data = DataService(db, _build_quote_client(), news_ttl_days=settings.news_cache_ttl_days)
        report = compute_outcomes_for_pending_events(db, data)
        log.info(
            "outcome_computation_done",
            events_examined=report.events_examined,
            outcomes_inserted=report.outcomes_inserted,
            skipped_horizon_in_future=report.skipped_horizon_in_future,
            skipped_data_unavailable=report.skipped_data_unavailable,
            skipped_benchmark_unavailable=report.skipped_benchmark_unavailable,
            skipped_already_computed=report.skipped_already_computed,
            failed=report.failed,
            failure_log_count=len(report.failure_log),
        )
        record_run_summary(db, {
            "ran_at": datetime.now(UTC).isoformat(),
            "inserted": report.outcomes_inserted,
            "skipped": (
                report.skipped_horizon_in_future
                + report.skipped_already_computed
                + report.skipped_data_unavailable
            ),
            "failed": report.failed,
        })
    finally:
        db.close()


def run_flex_sync() -> None:
    """Daily job: Phase 7a-Flex broker truth capture via IBKR Flex Web Service.

    Pulls the configured Activity Flex Query and persists snapshot rows
    into broker_account_snapshot / broker_cash_snapshot / broker_position_snapshot
    / broker_execution_snapshot. Open-orders are not produced by Flex (Phase
    7a-Flex spec L18).

    No-op (logs a warning, skips the call) when IBKR_FLEX_TOKEN or
    IBKR_FLEX_QUERY_ID are not configured — keeps the scheduler quiet on
    dev / pre-Flex-setup environments.

    Runs daily at 23:30 NY (after US market close + Flex generation buffer)
    by default; overridable via FLEX_SYNC_HOUR / FLEX_SYNC_MINUTE env.
    """
    from marketpulse.broker.flex_client import FlexClient
    from marketpulse.broker.readonly_sync import FlexSyncConfig, run_readonly_sync
    from marketpulse.trading.calendar import NY, NYTradingCalendar

    settings = get_settings()
    if not settings.ibkr_flex_token or not settings.ibkr_flex_query_id:
        log.warning(
            "flex_sync_skipped_unconfigured",
            reason="IBKR_FLEX_TOKEN or IBKR_FLEX_QUERY_ID not set",
        )
        return

    # Skip non-trading days (weekends + US market holidays). IBKR Flex
    # statements don't generate new truth on closed days — running anyway
    # produces FlexSendRequestError 1001 noise and a false failure row in
    # broker_sync_run. Skip happens BEFORE the SendRequest call so we never
    # talk to IBKR on closed days. Observed 2026-05-25 (Memorial Day) +
    # 2026-05-26 03:30 UTC failure that triggered this hardening.
    today_ny = datetime.now(UTC).astimezone(NY).date()
    if not NYTradingCalendar().is_business_day(today_ny):
        log.info(
            "flex_sync_skipped_market_closed",
            ny_date=today_ny.isoformat(),
            reason="NY market closed (weekend or holiday)",
        )
        return

    gen = session_scope()
    db = next(gen)
    try:
        config = FlexSyncConfig(
            token=settings.ibkr_flex_token,
            query_id=settings.ibkr_flex_query_id,
            base_url=settings.ibkr_flex_base_url,
            account_id=settings.ibkr_account_id or None,
            poll_interval_seconds=settings.ibkr_flex_poll_interval_seconds,
            max_wait_seconds=settings.ibkr_flex_max_wait_seconds,
            allow_live=settings.ibkr_allow_live,
        )
        with FlexClient(
            token=config.token,
            query_id=config.query_id,
            account_id=config.account_id,
            base_url=config.base_url,
            poll_interval_seconds=config.poll_interval_seconds,
            max_wait_seconds=config.max_wait_seconds,
        ) as client:
            result = run_readonly_sync(
                db, client=client, config=config, now=datetime.now(UTC),
            )
        db.commit()
        log.info(
            "flex_sync_done",
            sync_run_id=result.sync_run_id,
            status=result.status,
            account_id=result.account_id,
            error_type=result.error_type,
            error_message=result.error_message,
            account_snapshots=result.account_snapshots,
            cash_rows=result.cash_rows,
            positions=result.positions,
            executions=result.executions,
            reference_code=result.reference_code,
        )
        record_run_summary(db, {
            "ran_at": datetime.now(UTC).isoformat(),
            "status": result.status,
            "sync_run_id": result.sync_run_id,
            "rows_total": (
                result.account_snapshots + result.cash_rows
                + result.positions + result.executions
            ),
        })
    finally:
        db.close()


# Transient IBKR / network errors worth retrying once. Anything outside this
# set (auth, config, schema mismatch) is a hard fault — retry would just
# fail the same way, so we don't waste a request.
_FLEX_RETRYABLE_ERROR_TYPES = frozenset({
    "FlexHttpError",          # ConnectTimeout, 5xx, network hiccups
    "FlexSendRequestError",   # IBKR 1001 "statement could not be generated"
    "FlexReportTimeoutError", # statement never finishes generating
    "IbkrApiError",           # TWS/Gateway transient errors
})


def run_flex_sync_retry() -> None:
    """Retry flex sync once if the most recent run today failed with a
    transient error. Skips when last run succeeded or failed with a
    non-retryable (auth/config/schema) error. Fires 30min after the main
    flex_sync cron — gives IBKR's "statement could not be generated"
    backoff window time to clear.
    """
    from marketpulse.broker.flex_client import (
        FlexClient,  # noqa: F401  # import-graph parity with main job
    )
    from marketpulse.db.models import BrokerSyncRun
    from marketpulse.trading.calendar import NY, NYTradingCalendar

    settings = get_settings()
    if not settings.ibkr_flex_token or not settings.ibkr_flex_query_id:
        return

    today_ny = datetime.now(UTC).astimezone(NY).date()
    if not NYTradingCalendar().is_business_day(today_ny):
        return

    gen = session_scope()
    db = next(gen)
    try:
        # "Today" by NY date — the cron is registered in NY tz, so a run
        # from <= 24h ago in NY is the one we'd be retrying.
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        last = (
            db.query(BrokerSyncRun)
            .filter(BrokerSyncRun.started_at >= cutoff)
            .order_by(BrokerSyncRun.started_at.desc())
            .first()
        )
        if last is None:
            log.info("flex_sync_retry_skipped", reason="no_run_today")
            return
        if last.status != "failed":
            log.info(
                "flex_sync_retry_skipped",
                reason="last_run_not_failed",
                last_status=last.status,
            )
            return
        if last.error_type not in _FLEX_RETRYABLE_ERROR_TYPES:
            log.info(
                "flex_sync_retry_skipped",
                reason="non_retryable_error",
                error_type=last.error_type,
            )
            return
        log.info(
            "flex_sync_retry_firing",
            previous_error_type=last.error_type,
            previous_run_id=last.id,
        )
    finally:
        db.close()
    # Run the main sync. It opens its own session.
    run_flex_sync()


def build_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    sched = BackgroundScheduler(timezone="America/New_York")
    t = parse_recap_time(settings.watchlist_recap_time)
    sched.add_job(
        run_daily_recap,
        trigger=CronTrigger(hour=t.hour, minute=t.minute, day_of_week="mon-fri"),
        id="daily_recap", replace_existing=True, misfire_grace_time=600,
    )
    # 30-minute retry once if previous run failed (the recap service is idempotent).
    # Roll the hour over when minute+30 wraps past 60 so retry runs AFTER the main job.
    retry_hour = (t.hour + (1 if t.minute + 30 >= 60 else 0)) % 24
    retry_minute = (t.minute + 30) % 60
    sched.add_job(
        run_daily_recap,
        trigger=CronTrigger(hour=retry_hour, minute=retry_minute, day_of_week="mon-fri"),
        id="daily_recap_retry", replace_existing=True, misfire_grace_time=600,
    )
    sched.add_job(
        run_news_purge,
        trigger=CronTrigger(day_of_week="sun", hour=3),
        id="news_purge", replace_existing=True,
    )
    # Fill missing Holding.sector daily at 04:00 UTC (off-peak, pre-NY open).
    # Was inline on every /holdings GET — biggest cold-cache delay on that
    # route. Sector data is stable, so a 24h staleness window is fine.
    sched.add_job(
        run_sector_backfill,
        trigger=CronTrigger(hour=4, minute=0, timezone="UTC"),
        id="sector_backfill",
        replace_existing=True,
        misfire_grace_time=None,
        coalesce=True,
    )
    # Charter top-3 priority #1: SQLite safety floor. Daily 09:00 UTC
    # snapshot (= 05:00 NY pre-market) — low-traffic window, before any
    # paper_trading_tick activity. misfire_grace_time=None + coalesce
    # so a missed run during deploy gets caught up on next start.
    sched.add_job(
        run_db_backup,
        trigger=CronTrigger(hour=9, minute=0, timezone="UTC"),
        id="db_backup",
        replace_existing=True,
        misfire_grace_time=None,
        coalesce=True,
    )
    # PR3b: weekly charter review. Runs every Monday 09:30 UTC, AFTER the
    # 09:00 UTC db_backup so the report reads a fresh backup manifest.
    sched.add_job(
        run_charter_review_weekly,
        trigger=CronTrigger(
            day_of_week="mon", hour=9, minute=30, timezone="UTC",
        ),
        id="charter_review_weekly",
        replace_existing=True,
        misfire_grace_time=None,
        coalesce=True,
    )
    # Daily split-detection: runs once at 17:00 ET (after the daily recap)
    # so any same-day splits show up in the next morning's view.
    sched.add_job(
        run_detect_corporate_actions,
        trigger=CronTrigger(hour=17, minute=0, day_of_week="mon-fri"),
        id="detect_corporate_actions", replace_existing=True, misfire_grace_time=3600,
    )
    # Alert checker: every 5 min during US market hours (Mon-Fri 09:30-16:00 ET)
    sched.add_job(
        run_alert_check,
        trigger=CronTrigger(
            day_of_week="mon-fri", hour="9-16", minute="*/5",
        ),
        id="alert_check", replace_existing=True, misfire_grace_time=60,
    )
    # misfire_grace_time=None on daily critical jobs: if a deploy/restart pushes
    # us past the scheduled fire time, APScheduler should still run the job
    # exactly once on next start (coalesce=True merges multiple missed instances).
    # 3600s was dropping entire days during weekend deploys — observed 2026-05-25
    # when paper_trading_tick silently lost 5/22 Fri / 5/23 Sat / 5/24 Sun after
    # PR deploys restarted the container past the 21:30 ET schedule.
    # Outcome computation: daily 02:00 UTC (US close ~21:00 UTC → 5h buffer)
    sched.add_job(
        run_outcome_computation,
        trigger=CronTrigger(hour=2, minute=0, timezone="UTC"),
        id="outcome_computation",
        replace_existing=True,
        misfire_grace_time=None,
        coalesce=True,
    )
    # Phase 7a-Flex daily broker truth capture. Runs after US close + Flex
    # generation buffer. Skips silently if FLEX_TOKEN/QUERY_ID unset.
    sched.add_job(
        run_flex_sync,
        trigger=CronTrigger(
            hour=settings.flex_sync_hour,
            minute=settings.flex_sync_minute,
            day_of_week="mon-fri",  # no point on weekends; IBKR markets closed
            timezone=ZoneInfo("America/New_York"),
        ),
        id="flex_sync",
        replace_existing=True,
        misfire_grace_time=None,
        coalesce=True,
        max_instances=1,
    )
    # 30-minute retry once if the main run failed with a transient IBKR
    # error (1001 / ConnectTimeout / 5xx). run_flex_sync_retry() inspects
    # the most recent broker_sync_run row and decides whether to fire.
    # Roll the hour over when minute+30 wraps past 60.
    fs_retry_hour = (settings.flex_sync_hour + (
        1 if settings.flex_sync_minute + 30 >= 60 else 0
    )) % 24
    fs_retry_minute = (settings.flex_sync_minute + 30) % 60
    sched.add_job(
        run_flex_sync_retry,
        trigger=CronTrigger(
            hour=fs_retry_hour,
            minute=fs_retry_minute,
            day_of_week="mon-fri",
            timezone=ZoneInfo("America/New_York"),
        ),
        id="flex_sync_retry",
        replace_existing=True,
        misfire_grace_time=None,
        coalesce=True,
        max_instances=1,
    )
    # Phase 6a paper trading daily tick (lock xxv: thin entrypoint).
    # Imported here so the registration sits with all other jobs.
    from marketpulse.scheduler.paper_trading_tick import (
        _RISK_GATES_YAML,
        _STRATEGIES_DIR,
        paper_trading_tick_job,
    )
    from marketpulse.trading.risk_gates import (
        RiskConfigProvider,
        validate_paper_tick_in_placement_window,
    )
    # Startup invariant: the paper_tick wall-clock must align with the
    # MarketHoursGate placement window — otherwise the cron fires and
    # every order gets rejected with outside_placement_window, silently
    # losing trading days (root cause of the Phase 6 silent-no-orders
    # bug fixed in PR #117). Fail fast at boot rather than at 17:30 NY.
    _provider = RiskConfigProvider.from_yaml(
        global_path=_RISK_GATES_YAML, strategies_dir=_STRATEGIES_DIR,
    )
    validate_paper_tick_in_placement_window(
        tick_hour=settings.paper_tick_hour,
        tick_minute=settings.paper_tick_minute,
        cfg=_provider.global_config().market_hours,
    )
    sched.add_job(
        paper_trading_tick_job,
        trigger=CronTrigger(
            hour=settings.paper_tick_hour,
            minute=settings.paper_tick_minute,
            timezone=ZoneInfo("America/New_York"),
        ),
        id="paper_trading_tick",
        replace_existing=True,
        misfire_grace_time=None,
        coalesce=True,
        max_instances=1,
    )
    return sched
