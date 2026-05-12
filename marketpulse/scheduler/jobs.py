from datetime import date, time, timedelta

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
from marketpulse.data.tencent_client import TencentClient
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
        )
        svc = RecapService(db, data=data, ai=ai)
        result = svc.generate(target)
        log.info("recap_job_done", date=str(target), status=result.generation_status)

        # Optional push — non-blocking, never fails the job.
        # Skip if the recap itself failed: an empty body has no value.
        if settings.notifier_recap_enabled and result.generation_status == "ok":
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
        elif settings.notifier_recap_enabled and result.generation_status != "ok":
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
    correctly. recompute_ticker is only called when at least one new split
    actually landed.
    """
    log.info("detect_corporate_actions_start")
    tencent = TencentClient()
    yf_client = YFinanceClient()
    today = date.today()
    since = today - timedelta(days=1825)  # ~5 years lookback

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
                continue  # both sources logged + failed

            recompute_needed = False

            # Splits: record for all tickers (watchlist-only included).
            for ex_date, ratio in actions.splits:
                try:
                    record_split(
                        db, ticker=t, ex_date=ex_date, ratio=ratio, source=src,
                    )
                    log.info("split_recorded", ticker=t,
                             ex_date=str(ex_date), ratio=ratio, source=src)
                    recompute_needed = True
                except SplitError:
                    pass  # already recorded

            # Dividends: only record when shares held on ex_date.
            for ex_date, per_share in actions.dividends:
                qty = quantity_as_of(db, t, ex_date)
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
                except DividendError:
                    pass  # already recorded

            if recompute_needed:
                recompute_ticker(db, t)
    finally:
        db.close()
    log.info("detect_corporate_actions_done")


def _fetch_corp_actions(ticker, tencent, yf_client, since, today):
    """Try Tencent first; fall back to yfinance on any exception.
    Returns (CorporateActions, source_label) or (None, "none") on total failure.
    Never raises.
    """
    from marketpulse.data.tencent_client import CorporateActions
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
    return sched
