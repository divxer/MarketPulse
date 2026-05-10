from datetime import date, time

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
        data = DataService(db, YFinanceClient(), news_ttl_days=settings.news_cache_ttl_days)
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
    return sched
