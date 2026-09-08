from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import ensure_sqlite_parent_dir


def run_migrations(settings: Settings) -> None:
    ensure_sqlite_parent_dir(settings)
    alembic_ini, script_location = migration_paths()
    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(script_location))
    config.attributes["settings"] = settings
    command.upgrade(config, "head")


def migration_paths() -> tuple[Path, Path]:
    package_root = Path(__file__).resolve().parents[2]
    for base_path in (Path.cwd(), package_root):
        alembic_ini = base_path / "alembic.ini"
        script_location = base_path / "alembic"
        if alembic_ini.exists() and script_location.exists():
            return alembic_ini, script_location

    return package_root / "alembic.ini", package_root / "alembic"
