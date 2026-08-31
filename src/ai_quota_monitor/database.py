from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from ai_quota_monitor.config import Settings


def create_database_engine(settings: Settings) -> Engine:
    ensure_sqlite_parent_dir(settings)
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(settings.database_url, connect_args=connect_args)


def ensure_sqlite_parent_dir(settings: Settings) -> None:
    sqlite_prefix = "sqlite:///"
    if not settings.database_url.startswith(sqlite_prefix):
        return

    database_path = settings.database_url.removeprefix(sqlite_prefix)
    if database_path == ":memory:":
        return

    Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


def initialize_database(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("SELECT 1"))


def database_is_healthy(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return False

    return True
