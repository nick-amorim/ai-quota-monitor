from __future__ import annotations

from datetime import time

from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.models import Account, EventLog
from ai_quota_monitor.services.accounts import archive_account, create_account, seed_defaults
from ai_quota_monitor.services.anchors import AnchorAlreadyRunningError, update_app_setting
from ai_quota_monitor.services.scheduler import QuotaScheduler, daily_anchor_times
from ai_quota_monitor.services.smart_anchors import SmartAnchorResult


class FakeScheduledAnchorRunner:
    def __init__(self, decision: str = "sent", verification_status: str = "verified"):
        self.calls = []
        self.decision = decision
        self.verification_status = verification_status

    def run_scheduled_anchor(self, account_id: int, kind: str):
        self.calls.append((account_id, kind))
        return SmartAnchorResult(
            account_id=account_id,
            kind=kind,
            decision=self.decision,
            reason="test runner",
            verification_status=self.verification_status,
            anchor_run_id=1 if self.decision == "sent" else None,
        )


class BusyAnchorService:
    def run_scheduled_anchor(self, account_id: int, kind: str):
        raise AnchorAlreadyRunningError(f"Anchor already running for account {account_id}")


class FakeTelemetryRefresher:
    def __init__(self):
        self.calls = 0

    def refresh_connected_accounts(self):
        self.calls += 1
        return []


def make_scheduler(tmp_path, anchor_runner=None):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ai-quota-monitor.sqlite3'}",
        data_dir=tmp_path,
        missed_anchor_grace_minutes=30,
    )
    run_migrations(settings)
    engine = create_database_engine(settings)
    initialize_database(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        seed_defaults(session, settings)
        for account in session.query(Account).all():
            account.auth_status = "connected"
        session.commit()

    if anchor_runner is None:
        anchor_runner = FakeScheduledAnchorRunner()

    scheduler = QuotaScheduler(session_factory, anchor_runner, settings)
    scheduler.start()
    return engine, session_factory, scheduler, anchor_runner


def test_scheduler_creates_database_driven_daily_jobs(tmp_path):
    engine, session_factory, scheduler, _ = make_scheduler(tmp_path)

    try:
        jobs = scheduler.next_runs()
        daily_jobs = [job for job in jobs if job.kind == "daily"]

        assert {job.kind for job in jobs} == {"daily"}
        assert len(daily_jobs) == 7
        assert {job.sequence for job in daily_jobs if job.account_id == 1} == {0, 1, 2, 3}
        assert {job.sequence for job in daily_jobs if job.account_id == 2} == {0, 1, 2}
        assert all(job.next_run_at is not None for job in jobs)
        assert all(
            job.misfire_grace_time == 30 * 60
            for job in scheduler._scheduler.get_jobs()
        )

        with session_factory() as session:
            events = session.query(EventLog).all()

        assert any(event.message == "Scheduler jobs reloaded" for event in events)
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_scheduler_reload_adds_created_accounts_and_skips_archived(tmp_path):
    engine, session_factory, scheduler, _ = make_scheduler(tmp_path)

    try:
        with session_factory() as session:
            created = create_account(
                session,
                scheduler._settings,
                name="Work",
                daily_anchor_time=time(13, 0),
            )
            created.auth_status = "connected"
            first = session.get(Account, 1)
            assert first is not None
            archive_account(session, first)
            session.commit()

        scheduler.reload()
        jobs = scheduler.next_runs()
        names = {job.account_name for job in jobs}

        assert "Account A" not in names
        assert "Account B" in names
        assert "Work" in names
        assert any(job.account_name == "Work" and job.kind == "daily" for job in jobs)
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_scheduler_reload_reflects_schedule_changes_without_restart(tmp_path):
    engine, session_factory, scheduler, _ = make_scheduler(tmp_path)

    try:
        with session_factory() as session:
            account = session.get(Account, 1)
            assert account is not None
            assert account.schedule is not None
            account.schedule.daily_anchor_time = time(6, 30)
            account.schedule.monday_enabled = True
            account.schedule.tuesday_enabled = False
            account.schedule.wednesday_enabled = False
            account.schedule.thursday_enabled = False
            account.schedule.friday_enabled = False
            session.commit()

        scheduler.reload()
        daily = next(
            job
            for job in scheduler.next_runs()
            if job.account_id == 1 and job.kind == "daily"
        )

        assert daily.next_run_at is not None
        assert daily.next_run_at.hour == 6
        assert daily.next_run_at.minute == 30
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_scheduler_reload_removes_jobs_for_paused_account(tmp_path):
    engine, session_factory, scheduler, _ = make_scheduler(tmp_path)

    try:
        with session_factory() as session:
            account = session.get(Account, 1)
            assert account is not None
            assert account.schedule is not None
            account.schedule.anchor_paused = True
            session.commit()

        scheduler.reload()
        jobs = scheduler.next_runs()

        assert not any(job.account_id == 1 for job in jobs)
        assert any(job.account_id == 2 for job in jobs)
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_scheduler_skip_missed_policy_uses_minimal_grace(tmp_path):
    engine, session_factory, scheduler, _ = make_scheduler(tmp_path)

    try:
        with session_factory() as session:
            update_app_setting(session, "missed_anchor_policy", "skip_missed")

        scheduler.reload()

        assert all(job.misfire_grace_time == 1 for job in scheduler._scheduler.get_jobs())
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_scheduled_anchor_job_records_success_events(tmp_path):
    engine, session_factory, scheduler, runner = make_scheduler(tmp_path)

    try:
        scheduler.run_anchor_job(1, "daily")

        with session_factory() as session:
            messages = [event.message for event in session.query(EventLog).all()]

        assert "Scheduled daily anchor started" in messages
        assert "Scheduled daily anchor completed" in messages
        assert runner.calls == [(1, "daily")]
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_scheduled_anchor_job_logs_concurrency_skip(tmp_path):
    engine, session_factory, scheduler, _ = make_scheduler(
        tmp_path,
        anchor_runner=BusyAnchorService(),
    )

    try:
        scheduler.run_anchor_job(1, "daily")

        with session_factory() as session:
            event = (
                session.query(EventLog)
                .filter(EventLog.message.like("%skipped because another run is active"))
                .one()
            )

        assert event.level == "warning"
        assert event.account_id == 1
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_scheduler_adds_automatic_usage_refresh_job(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ai-quota-monitor.sqlite3'}",
        data_dir=tmp_path,
        usage_poll_interval_minutes=2,
    )
    run_migrations(settings)
    engine = create_database_engine(settings)
    initialize_database(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        seed_defaults(session, settings)

    telemetry = FakeTelemetryRefresher()
    scheduler = QuotaScheduler(
        session_factory,
        FakeScheduledAnchorRunner(),
        settings,
        telemetry,
    )
    scheduler.start()

    try:
        job = scheduler._scheduler.get_job("telemetry:refresh-all")
        assert job is not None
        assert job.trigger.interval.total_seconds() == 120

        scheduler.run_usage_refresh_job()

        assert telemetry.calls >= 1
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_daily_anchor_times_follow_five_hour_cadence_until_day_end():
    assert daily_anchor_times(time(5, 0)) == (
        time(5, 0),
        time(10, 0),
        time(15, 0),
        time(20, 0),
    )
    assert daily_anchor_times(time(9, 0)) == (
        time(9, 0),
        time(14, 0),
        time(19, 0),
    )
