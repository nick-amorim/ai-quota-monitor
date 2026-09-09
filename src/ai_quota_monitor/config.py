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
    log_file: Path | None = None
    log_max_bytes: int = 1_000_000
    log_backup_count: int = 3

    usage_poll_interval_minutes: int = 5
    enable_scheduler: bool = True
    enable_app_server_notifications: bool = True
    anchor_prompt: str = "Reply only with OK."
    anchor_verification_timeout_seconds: int = 60
    missed_anchor_policy: str = "run_if_within_grace"
    missed_anchor_grace_minutes: int = 30
    history_retention_days: int = 90
    dashboard_refresh_interval_seconds: int = 10
    deployment_mode: str = "auto"
    install_dir: Path = Field(default_factory=lambda: Path("/opt/ai-quota-monitor"))
    backup_dir: Path = Field(default_factory=lambda: Path("backups"))
    update_remote: str = "origin"
    update_branch: str = "main"
    update_service_name: str = "ai-quota-monitor"
    enable_web_updates: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AI_QUOTA_MONITOR_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
