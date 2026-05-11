from datetime import date, time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from marketpulse.ai.client import AnthropicClient
from marketpulse.ai.service import AiService
from marketpulse.alerts.engine import evaluate_rules
from marketpulse.alerts.notifier import build_notifier
from marketpulse.config import get_settings
from marketpulse.data.cache import NewsCache
from marketpulse.data.hybrid_client import HybridClient
from marketpulse.data.service import DataService
from marketpulse.data.tencent_client import TencentClient
from marketpulse.data.yfinance_client import YFinanceClient
from marketpulse.db.base import session_scope
from marketpulse.logging import get_logger
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
        )
        svc = RecapService(db, data=data, ai=ai)
        result = svc.generate(target)
        log.info("recap_job_done", date=str(target), status=result.generation_status)
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
    # Alert checker: every 5 min during US market hours (Mon-Fri 09:30-16:00 ET)
    sched.add_job(
        run_alert_check,
        trigger=CronTrigger(
            day_of_week="mon-fri", hour="9-16", minute="*/5",
        ),
        id="alert_check", replace_existing=True, misfire_grace_time=60,
    )
    return sched
