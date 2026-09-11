from __future__ import annotations

from datetime import UTC, datetime

from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.models import UsageSnapshot
from ai_quota_monitor.services.accounts import list_accounts, seed_defaults
from ai_quota_monitor.services.monitor import (
    build_monitor_view,
    format_local_datetime,
    format_local_time,
)
from ai_quota_monitor.services.scheduler import ScheduledAnchorJob


def make_accounts(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ai-quota-monitor.sqlite3'}",
        data_dir=tmp_path,
        timezone="UTC",
    )
    run_migrations(settings)
    engine = create_database_engine(settings)
    initialize_database(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        seed_defaults(session, settings)
        accounts = list_accounts(session)
    return engine, settings, accounts


def test_monitor_view_uses_observed_weekly_reset_and_today_timeline(tmp_path):
    engine, settings, accounts = make_accounts(tmp_path)
    now = datetime(2026, 1, 5, 1, 0, tzinfo=UTC)
    snapshot = UsageSnapshot(
        account_id=1,
        captured_at=now,
        five_hour_used_percent=72,
        five_hour_window_minutes=300,
        five_hour_reset_at=None,
        five_hour_expected_reset_at=datetime(2026, 1, 5, 10, 0, tzinfo=UTC),
        five_hour_observed_reset_at=datetime(2026, 1, 5, 10, 0, tzinfo=UTC),
        weekly_used_percent=43,
        weekly_window_minutes=10080,
        weekly_reset_at=None,
        weekly_expected_reset_at=datetime(2026, 1, 5, 5, 0, tzinfo=UTC),
        weekly_observed_reset_at=datetime(2026, 1, 5, 6, 0, tzinfo=UTC),
        parser_status="ok",
        parser_message=None,
        source="test",
    )
    scheduled_job = ScheduledAnchorJob(
        id="anchor:1:daily",
        account_id=1,
        account_name="Account A",
        kind="daily",
        next_run_at=datetime(2026, 1, 5, 5, 0, tzinfo=UTC),
        timezone="UTC",
    )

    try:
        view = build_monitor_view(
            accounts=accounts,
            usage_by_account={1: snapshot},
            scheduled_jobs=[scheduled_job],
            settings=settings,
            now=now,
        )

        account = view.account_map[1]
        five_hour, weekly = account.windows

        assert view.generated_clock_label == "01:00"
        assert account.display_label == "Account not logged in"
        assert account.daily_schedule_label == "Mon-Fri 05:00, 10:00, 15:00, 20:00"
        assert account.anchors_paused is False
        assert account.next_wake_label == "Daily Mon 05:00"
        assert five_hour.short_label == "5h"
        assert five_hour.remaining_percent_label == "28.0%"
        assert weekly.remaining_percent_label == "57.0%"
        assert five_hour.configured_short_label == "05:00"
        assert five_hour.reset_time_label == "10:00"
        assert five_hour.reset_source_short_label == "obs"
        assert weekly.short_label == "7d"
        assert weekly.configured_short_label is None
        assert weekly.expected_label == "Unknown"
        assert weekly.next_label == "Mon 06:00"
        assert five_hour.configured_label == "05:00 + 5h"
        assert five_hour.observed_label == "2026-01-05 10:00:00"
        assert any(entry.label == "Daily anchor" for entry in view.timeline)
        assert any(entry.label == "Observed weekly reset" for entry in view.timeline)
    finally:
        engine.dispose()


def test_monitor_view_marks_stale_snapshots(tmp_path):
    engine, settings, accounts = make_accounts(tmp_path)
    now = datetime(2026, 1, 5, 1, 0, tzinfo=UTC)
    snapshot = UsageSnapshot(
        account_id=1,
        captured_at=datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
        five_hour_used_percent=10,
        five_hour_window_minutes=300,
        weekly_used_percent=10,
        weekly_window_minutes=10080,
        parser_status="ok",
        source="test",
    )

    try:
        view = build_monitor_view(
            accounts=accounts,
            usage_by_account={1: snapshot},
            scheduled_jobs=[],
            settings=settings,
            now=now,
        )

        assert view.account_map[1].snapshot_status_class == "stale"
        assert view.account_map[1].windows[0].status_label == "Stale"
    finally:
        engine.dispose()


def test_monitor_view_marks_refresh_failure_without_snapshot(tmp_path):
    engine, settings, accounts = make_accounts(tmp_path)

    try:
        view = build_monitor_view(
            accounts=accounts,
            usage_by_account={},
            telemetry_errors_by_account={1: "Codex CLI is not available"},
            scheduled_jobs=[],
            settings=settings,
            now=datetime(2026, 1, 5, 1, 0, tzinfo=UTC),
        )

        account = view.account_map[1]
        assert account.telemetry_error == "Codex CLI is not available"
        assert account.snapshot_status_label == "Telemetry error"
        assert account.snapshot_status_class == "error"
        assert account.windows[0].status_label == "Error"
        assert account.windows[0].status_class == "error"
    finally:
        engine.dispose()


def test_local_time_formatters_treat_naive_database_values_as_utc():
    value = datetime(2026, 9, 9, 11, 46, 37)

    assert format_local_time(value, "America/Recife") == "08:46"
    assert format_local_datetime(value, "America/Recife") == "2026-09-09 08:46:37"
