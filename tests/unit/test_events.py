from __future__ import annotations

from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.services.accounts import seed_defaults
from ai_quota_monitor.services.events import EventFilters, list_events, record_event


def make_session_factory(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ai-quota-monitor.sqlite3'}",
        data_dir=tmp_path,
    )
    run_migrations(settings)
    engine = create_database_engine(settings)
    initialize_database(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        seed_defaults(session, settings)
    return engine, session_factory


def test_list_events_filters_by_account_level_and_category(tmp_path):
    engine, session_factory = make_session_factory(tmp_path)

    try:
        with session_factory() as session:
            record_event(
                session,
                level="info",
                category="scheduler.reload",
                message="Scheduler reloaded",
                account_id=1,
            )
            record_event(
                session,
                level="warning",
                category="live-updates.start",
                message="Listener warning",
                account_id=1,
            )
            record_event(
                session,
                level="warning",
                category="live-updates.start",
                message="Other account warning",
                account_id=2,
            )

        filtered = list_events(
            session_factory,
            filters=EventFilters(
                account_id=1,
                level="warning",
                category="live-updates",
            ),
            limit=20,
        )

        assert [event.message for event in filtered] == ["Listener warning"]
        assert filtered[0].account is not None
        assert filtered[0].account.name == "Account A"
    finally:
        engine.dispose()
