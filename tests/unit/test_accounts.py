from __future__ import annotations

from datetime import time

from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.services.accounts import (
    default_app_settings,
    ensure_runtime_directories,
    get_account,
    list_accounts,
    seed_defaults,
    update_account_schedule,
)
from ai_quota_monitor.models import AppSetting


def make_session(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ai-quota-monitor.sqlite3'}",
        data_dir=tmp_path,
    )
    run_migrations(settings)
    engine = create_database_engine(settings)
    initialize_database(engine)
    return settings, engine, create_session_factory(engine)


def test_seed_defaults_creates_two_accounts_with_expected_schedules(tmp_path):
    settings, engine, session_factory = make_session(tmp_path)

    with session_factory() as session:
        seed_defaults(session, settings)
        accounts = list_accounts(session)

    assert [account.slug for account in accounts] == ["account-a", "account-b"]
    assert accounts[0].schedule.daily_anchor_time == time(5, 0)
    assert accounts[0].schedule.weekly_target_day == "monday"
    assert accounts[1].schedule.daily_anchor_time == time(9, 0)
    assert accounts[1].schedule.weekly_target_day == "wednesday"
    engine.dispose()


def test_seed_defaults_is_idempotent(tmp_path):
    settings, engine, session_factory = make_session(tmp_path)

    with session_factory() as session:
        seed_defaults(session, settings)
        seed_defaults(session, settings)
        accounts = list_accounts(session)

    assert len(accounts) == 2
    engine.dispose()


def test_seed_defaults_creates_app_settings(tmp_path):
    settings, engine, session_factory = make_session(tmp_path)

    with session_factory() as session:
        seed_defaults(session, settings)

        for key, value in default_app_settings(settings).items():
            setting = session.get(AppSetting, key)
            assert setting is not None
            assert setting.value == value

    engine.dispose()


def test_seed_defaults_uses_configured_timezone_for_app_settings(tmp_path):
    settings, engine, session_factory = make_session(tmp_path)
    settings.timezone = "America/Fortaleza"

    with session_factory() as session:
        seed_defaults(session, settings)
        setting = session.get(AppSetting, "timezone")

    assert setting is not None
    assert setting.value == "America/Fortaleza"
    engine.dispose()


def test_ensure_runtime_directories_creates_account_paths(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ai-quota-monitor.sqlite3'}",
        data_dir=tmp_path,
    )

    ensure_runtime_directories(settings)

    assert (tmp_path / "account-a" / "codex-home").is_dir()
    assert (tmp_path / "account-a" / "workspace").is_dir()
    assert (tmp_path / "account-b" / "codex-home").is_dir()
    assert (tmp_path / "account-b" / "workspace").is_dir()


def test_update_account_schedule_persists(tmp_path):
    settings, engine, session_factory = make_session(tmp_path)

    with session_factory() as session:
        seed_defaults(session, settings)
        account = list_accounts(session)[0]
        update_account_schedule(
            session,
            account,
            enabled=False,
            daily_anchor_enabled=False,
            daily_anchor_time=time(6, 30),
            weekly_target_day="friday",
            weekly_target_time=time(7, 45),
            timezone="America/Fortaleza",
            active_weekdays={"monday", "friday"},
            skip_if_window_active=False,
        )
        account_id = account.id

    with session_factory() as session:
        account = get_account(session, account_id)

    assert account is not None
    assert account.enabled is False
    assert account.schedule.daily_anchor_enabled is False
    assert account.schedule.daily_anchor_time == time(6, 30)
    assert account.schedule.weekly_target_day == "friday"
    assert account.schedule.weekly_target_time == time(7, 45)
    assert account.schedule.timezone == "America/Fortaleza"
    assert account.schedule.monday_enabled is True
    assert account.schedule.tuesday_enabled is False
    assert account.schedule.friday_enabled is True
    assert account.schedule.skip_if_window_active is False
    engine.dispose()
