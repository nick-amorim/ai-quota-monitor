from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import ensure_sqlite_parent_dir


def run_migrations(settings: Settings) -> None:
    ensure_sqlite_parent_dir(settings)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[2] / "alembic"),
    )
    config.attributes["settings"] = settings
    command.upgrade(config, "head")
