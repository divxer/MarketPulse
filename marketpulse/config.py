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
    ai_cache_ttl_hours: int = Field(24, alias="AI_CACHE_TTL_HOURS")
    news_cache_ttl_days: int = Field(7, alias="NEWS_CACHE_TTL_DAYS")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
