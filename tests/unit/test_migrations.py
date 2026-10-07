from __future__ import annotations

from datetime import UTC, datetime

from ai_quota_monitor.migrations import migration_paths
from alembic import command
from alembic.config import Config
from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import create_database_engine, create_session_factory
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.models import Account, AnchorRun
from ai_quota_monitor.services.accounts import seed_defaults


def test_migration_paths_prefer_working_directory(monkeypatch, tmp_path):
    (tmp_path / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    (tmp_path / "alembic").mkdir()
    monkeypatch.chdir(tmp_path)

    alembic_ini, script_location = migration_paths()

    assert alembic_ini == tmp_path / "alembic.ini"
    assert script_location == tmp_path / "alembic"


def test_recovery_migration_preserves_existing_accounts_schedules_and_history(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'migration.sqlite3'}", data_dir=tmp_path)
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    config.attributes["settings"] = settings
    command.upgrade(config, "20260911_0007")
    engine = create_database_engine(settings)
    sessions = create_session_factory(engine)
    with sessions() as session:
        seed_defaults(session, settings)
        session.add(
            AnchorRun(
                account_id=1,
                status="failed",
                prompt="OK",
                started_at=datetime.now(UTC),
            )
        )
        session.commit()
    run_migrations(settings)
    with sessions() as session:
        assert session.get(Account, 1) is not None
        assert session.get(Account, 1).schedule is not None
        assert session.query(AnchorRun).count() == 1
    engine.dispose()
