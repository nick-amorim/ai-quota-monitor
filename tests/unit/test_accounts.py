from __future__ import annotations

from datetime import time
from pathlib import Path

from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.services.accounts import (
    archive_account,
    create_account,
    default_app_settings,
    ensure_runtime_directories,
    get_account,
    list_accounts,
    seed_defaults,
    set_account_anchor_paused,
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
    assert [account.sort_order for account in accounts] == [1, 2]
    assert accounts[0].schedule.daily_anchor_time == time(5, 0)
    assert accounts[0].schedule.anchor_paused is False
    assert accounts[1].schedule.daily_anchor_time == time(9, 0)
    assert accounts[1].schedule.anchor_paused is False
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


def test_ensure_runtime_directories_creates_database_account_paths(tmp_path):
    settings, engine, session_factory = make_session(tmp_path)

    with session_factory() as session:
        seed_defaults(session, settings)
        account = create_account(
            session,
            settings,
            name="Work",
            daily_anchor_time=time(13, 0),
        )
        account_slug = account.slug
        ensure_runtime_directories(settings, session)

    assert (tmp_path / account_slug / "codex-home").is_dir()
    assert (tmp_path / account_slug / "workspace").is_dir()
    engine.dispose()


def test_seed_defaults_stores_absolute_runtime_paths(tmp_path):
    settings, engine, session_factory = make_session(tmp_path)

    with session_factory() as session:
        seed_defaults(session, settings)
        account = list_accounts(session)[0]

    assert Path(account.codex_home).is_absolute()
    assert Path(account.workspace_path).is_absolute()
    engine.dispose()


def test_create_account_adds_isolated_scheduled_account(tmp_path):
    settings, engine, session_factory = make_session(tmp_path)

    with session_factory() as session:
        seed_defaults(session, settings)
        account = create_account(
            session,
            settings,
            name="Work",
            daily_anchor_time=time(13, 0),
            timezone="America/Fortaleza",
            active_weekdays={"monday", "wednesday"},
        )
        created = get_account(session, account.id)
        accounts = list_accounts(session)

    assert [account.slug for account in accounts] == ["account-a", "account-b", "account-3"]
    assert created is not None
    assert created.name == "Work"
    assert created.schedule.daily_anchor_time == time(13, 0)
    assert created.schedule.anchor_paused is False
    assert created.schedule.timezone == "America/Fortaleza"
    assert created.schedule.monday_enabled is True
    assert created.schedule.tuesday_enabled is False
    assert created.schedule.wednesday_enabled is True
    assert (tmp_path / "account-3" / "codex-home").is_dir()
    assert (tmp_path / "account-3" / "workspace").is_dir()
    engine.dispose()


def test_archive_account_hides_without_deleting_history_row(tmp_path):
    settings, engine, session_factory = make_session(tmp_path)

    with session_factory() as session:
        seed_defaults(session, settings)
        account = list_accounts(session)[0]
        account_id = account.id
        archive_account(session, account)

    with session_factory() as session:
        visible = list_accounts(session)
        all_accounts = list_accounts(session, include_archived=True)
        active_lookup = get_account(session, account_id)
        archived = get_account(session, account_id, include_archived=True)

    assert [account.slug for account in visible] == ["account-b"]
    assert [account.slug for account in all_accounts] == ["account-a", "account-b"]
    assert active_lookup is None
    assert archived is not None
    assert archived.archived_at is not None
    assert archived.enabled is False
    engine.dispose()


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
    assert account.schedule.timezone == "America/Fortaleza"
    assert account.schedule.monday_enabled is True
    assert account.schedule.tuesday_enabled is False
    assert account.schedule.friday_enabled is True
    assert account.schedule.skip_if_window_active is False
    engine.dispose()


def test_set_account_anchor_paused_preserves_monitoring_schedule(tmp_path):
    settings, engine, session_factory = make_session(tmp_path)

    with session_factory() as session:
        seed_defaults(session, settings)
        account = list_accounts(session)[0]
        set_account_anchor_paused(session, account, paused=True)
        account_id = account.id

    with session_factory() as session:
        account = get_account(session, account_id)

    assert account is not None
    assert account.enabled is True
    assert account.schedule.anchor_paused is True
    assert account.schedule.daily_anchor_enabled is True
    assert account.schedule.daily_anchor_time == time(5, 0)
    engine.dispose()
