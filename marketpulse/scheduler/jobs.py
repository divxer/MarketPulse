from datetime import UTC, date, datetime, time, timedelta

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
    # Outcome computation: daily 02:00 UTC (US close ~21:00 UTC → 5h buffer)
    sched.add_job(
        run_outcome_computation,
        trigger=CronTrigger(hour=2, minute=0, timezone="UTC"),
        id="outcome_computation",
        replace_existing=True,
    )
    return sched
