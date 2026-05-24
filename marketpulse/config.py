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
    # Optional premium model for /stock AI deep analysis only — daily recap and
    # portfolio risk stay on the cheap default. Empty string = use AI_MODEL.
    ai_model_analyze: str = Field("", alias="AI_MODEL_ANALYZE")
    # Phase 3: cheap model for the strategy router stage (Haiku-class).
    # Empty string = use AI_MODEL (which costs ~100x more — don't leave empty
    # in production).
    ai_model_router: str = Field("claude-haiku-4-5", alias="AI_MODEL_ROUTER")
    ai_cache_ttl_hours: int = Field(24, alias="AI_CACHE_TTL_HOURS", ge=0)
    news_cache_ttl_days: int = Field(7, alias="NEWS_CACHE_TTL_DAYS", ge=0)
    quote_cache_ttl_seconds: int = Field(300, alias="QUOTE_CACHE_TTL_SECONDS", ge=0)
    # Live quote source: 'auto' (Tencent first, yfinance fallback), 'tencent', 'yfinance'
    quote_source: str = Field("auto", alias="QUOTE_SOURCE")

    # Notifier: one of "none" | "bark" | "serverchan" | "smtp"
    notifier_kind: str = Field("none", alias="NOTIFIER_KIND")
    notifier_bark_url: str = Field("", alias="NOTIFIER_BARK_URL")
    notifier_serverchan_key: str = Field("", alias="NOTIFIER_SERVERCHAN_KEY")
    notifier_smtp_host: str = Field("", alias="NOTIFIER_SMTP_HOST")
    notifier_smtp_port: int = Field(587, alias="NOTIFIER_SMTP_PORT", ge=0)
    notifier_smtp_user: str = Field("", alias="NOTIFIER_SMTP_USER")
    notifier_smtp_password: str = Field("", alias="NOTIFIER_SMTP_PASSWORD")
    notifier_email_from: str = Field("", alias="NOTIFIER_EMAIL_FROM")
    notifier_email_to: str = Field("", alias="NOTIFIER_EMAIL_TO")
    notifier_recap_enabled: bool = Field(True, alias="NOTIFIER_RECAP_ENABLED")
    public_base_url: str = Field("", alias="PUBLIC_BASE_URL")

    # Alert debounce: don't re-fire the same rule within this many minutes
    alert_debounce_minutes: int = Field(60, alias="ALERT_DEBOUNCE_MINUTES", ge=0)

    # Phase 6a paper-trading settings
    paper_tick_hour: int = Field(17, alias="MP_PAPER_TICK_HOUR")
    paper_tick_minute: int = Field(30, alias="MP_PAPER_TICK_MINUTE")
    paper_initial_deposit: str = Field("10000", alias="MP_PAPER_INITIAL_DEPOSIT")
    paper_kill_switch: bool = Field(False, alias="MP_PAPER_KILL_SWITCH")
    # Phase 6g: master enable for paper-trading post-tick notifications.
    # Independent of NOTIFIER_RECAP_ENABLED (Phase 2). Lock 6g-L15.
    paper_notifications_enabled: bool = Field(
        True,
        alias="MP_PAPER_NOTIFICATIONS_ENABLED",
    )

    # Phase 7a-Flex IBKR settings (account_id + live-brake shared with Flex).
    ibkr_account_id: str = Field("", alias="IBKR_ACCOUNT_ID")
    ibkr_allow_live: bool = Field(False, alias="MP_IBKR_ALLOW_LIVE")

    # Phase 7a-Flex IBKR read-only sync via Flex Web Service.
    ibkr_flex_token: str = Field("", alias="IBKR_FLEX_TOKEN")
    ibkr_flex_query_id: int = Field(0, alias="IBKR_FLEX_QUERY_ID", ge=0)
    ibkr_flex_base_url: str = Field(
        "https://gdcdyn.interactivebrokers.com/Universal/servlet",
        alias="IBKR_FLEX_BASE_URL",
    )
    ibkr_flex_poll_interval_seconds: int = Field(
        5,
        alias="IBKR_FLEX_POLL_INTERVAL_SECONDS",
        ge=0,
    )
    ibkr_flex_max_wait_seconds: int = Field(
        60,
        alias="IBKR_FLEX_MAX_WAIT_SECONDS",
        ge=0,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
