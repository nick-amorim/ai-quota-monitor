from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ai-quota-monitor"
    env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8080
    timezone: str = "America/Recife"

    database_url: str = "sqlite:///./data/ai-quota-monitor.sqlite3"
    data_dir: Path = Field(default_factory=lambda: Path("data"))
    log_level: str = "INFO"

    usage_poll_interval_minutes: int = 5
    anchor_prompt: str = "Reply only with OK."
    anchor_verification_timeout_seconds: int = 60
    missed_anchor_grace_minutes: int = 30
    history_retention_days: int = 90

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AI_QUOTA_MONITOR_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
