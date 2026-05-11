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
    ai_cache_ttl_hours: int = Field(24, alias="AI_CACHE_TTL_HOURS", ge=0)
    news_cache_ttl_days: int = Field(7, alias="NEWS_CACHE_TTL_DAYS", ge=0)
    quote_cache_ttl_seconds: int = Field(300, alias="QUOTE_CACHE_TTL_SECONDS", ge=0)

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

    # Alert debounce: don't re-fire the same rule within this many minutes
    alert_debounce_minutes: int = Field(60, alias="ALERT_DEBOUNCE_MINUTES", ge=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
