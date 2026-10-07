from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ai_quota_monitor.models import Account, AnchorRun, DailyAnchorRecovery, EventLog, UsageRaw, UsageSnapshot
from ai_quota_monitor.services.recovery import (
    RecoveryReset,
    fresh_active_window_reset,
    fresh_blocking_reset,
    is_usage_limit_error,
    parse_usage_limit_reset,
    structured_reset_from_error,
)
from ai_quota_monitor.services.scheduler import QuotaScheduler, RECOVERY_KIND
from ai_quota_monitor.services.scheduler import _eligible_recovery_now
import ai_quota_monitor.services.scheduler as scheduler_module
from ai_quota_monitor.services.anchors import update_app_setting
from ai_quota_monitor.services.anchors import AnchorService
from ai_quota_monitor.services.smart_anchors import SmartAnchorResult
from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import create_database_engine, create_session_factory, initialize_database
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.services.accounts import seed_defaults


def _payload(*, five_used=100, five_reset=None, weekly_used=20, weekly_reset=None):
    return {"rateLimits": {"primary": {"usedPercent": five_used, "resetsAt": int(five_reset.timestamp()) if five_reset else None, "windowDurationMins": 300}, "secondary": {"usedPercent": weekly_used, "resetsAt": int(weekly_reset.timestamp()) if weekly_reset else None, "windowDurationMins": 10080}}}


def _snapshot(raw: UsageRaw, now: datetime) -> UsageSnapshot:
    return UsageSnapshot(account_id=1, captured_at=now, raw_usage_id=raw.id, parser_status="ok", source="test")


def test_reported_recife_bare_time_fallback_is_exactly_one_minute_after_reset():
    failed_at = datetime(2026, 10, 7, 18, 0, 4, tzinfo=UTC)  # 15:00:04 America/Recife
    reset = parse_usage_limit_reset(
        "You've hit your usage limit. Try again at 6:00 PM.",
        failed_at=failed_at,
        timezone_name="America/Recife",
    )

    assert reset is not None
    assert reset.reset_at == datetime(2026, 10, 7, 21, 0, tzinfo=UTC)
    assert reset.reset_at + timedelta(minutes=1) == datetime(2026, 10, 7, 21, 1, tzinfo=UTC)
    assert reset.detail["timezone_assumption"] == "America/Recife"


def test_fallback_rejects_unqualified_suffixes_and_accepts_usage_limit_without_time():
    failed_at = datetime(2026, 10, 7, 15, 0, tzinfo=UTC)
    assert parse_usage_limit_reset("usage limit exceeded: try again at 6:00 PM UTC", failed_at=failed_at, timezone_name="UTC") is None
    assert is_usage_limit_error(RuntimeError("You've hit your usage limit"))
    assert not is_usage_limit_error(RuntimeError("network unavailable"))
    assert not is_usage_limit_error(RuntimeError("network error reading usage limits"))


def test_fallback_handles_rollover_and_rejects_dst_wall_time_ambiguity():
    late = datetime(2026, 10, 7, 23, 30, tzinfo=UTC)
    reset = parse_usage_limit_reset("usage limit exceeded. try again at 12:05 AM", failed_at=late, timezone_name="UTC")
    assert reset is not None
    assert reset.reset_at == datetime(2026, 10, 8, 0, 5, tzinfo=UTC)
    ambiguous = parse_usage_limit_reset(
        "usage limit exceeded. try again at 1:30 AM",
        failed_at=datetime(2026, 11, 1, 4, 0, tzinfo=UTC),
        timezone_name="America/New_York",
    )
    assert ambiguous is None


def test_structured_metadata_overrides_text_and_rejects_naive_or_bool_values():
    now = datetime(2026, 10, 7, 15, 0, tzinfo=UTC)
    exc = RuntimeError("usage limit exceeded. try again at 6:00 PM")
    exc.data = {"codexErrorInfo": "UsageLimitExceeded", "resetsAt": int((now + timedelta(hours=5)).timestamp())}
    resolved = structured_reset_from_error(exc, now=now)
    assert resolved is not None
    assert resolved.source == "provider-error-metadata"
    exc.data = {"codexErrorInfo": "UsageLimitExceeded", "resetsAt": True}
    assert structured_reset_from_error(exc, now=now) is None
    exc.data = {"codexErrorInfo": "UsageLimitExceeded", "resetsAt": datetime(2026, 10, 7, 20, 0)}
    assert structured_reset_from_error(exc, now=now) is None


def test_fresh_telemetry_selects_later_weekly_reset_and_ignores_sparse_inheritance(tmp_path):
    now = datetime.now(UTC)
    five = now + timedelta(hours=2)
    weekly = now + timedelta(days=3)
    raw = UsageRaw(account_id=1, captured_at=now, parser_version="test", payload_json=json.dumps(_payload(five_reset=five, weekly_used=100, weekly_reset=weekly)))
    raw.id = 7
    snapshot = _snapshot(raw, now)
    snapshot.id = 9

    reset = fresh_blocking_reset(snapshot, raw, now=now)
    assert reset is not None
    assert reset.reset_at == weekly.replace(microsecond=0)

    sparse = UsageRaw(account_id=1, captured_at=now, parser_version="test", payload_json=json.dumps({"rateLimits": {"secondary": {"usedPercent": 20, "resetsAt": int(weekly.timestamp()), "windowDurationMins": 10080}}}))
    sparse.id = 8
    assert fresh_blocking_reset(snapshot, sparse, now=now) is None


def test_active_window_accepts_fresh_non_exhausted_five_hour_evidence():
    now = datetime.now(UTC)
    raw = UsageRaw(account_id=1, captured_at=now, parser_version="test", payload_json=json.dumps(_payload(five_used=30, five_reset=now + timedelta(hours=2), weekly_reset=now + timedelta(days=3))))
    raw.id = 7
    snapshot = _snapshot(raw, now)
    snapshot.id = 9
    reset = fresh_active_window_reset(snapshot, raw, now=now)
    assert reset is not None
    assert reset.source == "fresh-active-window-telemetry"


def test_active_window_uses_later_exhausted_weekly_reset():
    now = datetime.now(UTC)
    weekly = now + timedelta(days=4)
    raw = UsageRaw(account_id=1, captured_at=now, parser_version="test", payload_json=json.dumps(_payload(five_used=30, five_reset=now + timedelta(hours=1), weekly_used=100, weekly_reset=weekly)))
    raw.id = 7
    snapshot = _snapshot(raw, now)
    snapshot.id = 9
    reset = fresh_active_window_reset(snapshot, raw, now=now)
    assert reset is not None
    assert reset.reset_at == weekly.replace(microsecond=0)


def test_unknown_exhausted_weekly_reset_does_not_schedule_at_five_hour_reset():
    now = datetime.now(UTC)
    raw = UsageRaw(
        account_id=1,
        captured_at=now,
        parser_version="test",
        payload_json=json.dumps(_payload(five_used=100, five_reset=now + timedelta(hours=1), weekly_used=100)),
    )
    raw.id = 7
    snapshot = _snapshot(raw, now)
    snapshot.id = 9
    assert fresh_blocking_reset(snapshot, raw, now=now) is None
    assert fresh_active_window_reset(snapshot, raw, now=now) is None


class _Runner:
    def run_scheduled_anchor(self, account_id, kind):
        return SmartAnchorResult(account_id, kind, "sent", "test", "verified")


class _UsageLimitRunner:
    def run_scheduled_anchor(self, account_id, kind):
        raise RuntimeError("You've hit your usage limit. Try again at 6:00 PM.")


class _Telemetry:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def refresh_connected_accounts(self):
        return []

    def refresh_account_usage(self, account_id):
        return type("Result", (), {"snapshot": self.snapshot})()


class _QuotaBackend:
    def run_anchor(self, account, prompt):
        raise RuntimeError("You've hit your usage limit. Try again at 6:00 PM.")


class _AnchorServiceRunner:
    def __init__(self, service):
        self.service = service

    def run_scheduled_anchor(self, account_id, kind):
        return self.service.run_manual_anchor(account_id)


def _scheduler(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'quota.sqlite3'}", data_dir=tmp_path)
    run_migrations(settings)
    engine = create_database_engine(settings)
    initialize_database(engine)
    sessions = create_session_factory(engine)
    with sessions() as session:
        seed_defaults(session, settings)
        account = session.get(Account, 1)
        assert account is not None
        account.auth_status = "connected"
        assert account.schedule is not None
        for weekday in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
            setattr(account.schedule, f"{weekday}_enabled", True)
        session.commit()
    scheduler = QuotaScheduler(sessions, _Runner(), settings)
    scheduler.start()
    return engine, sessions, scheduler


def test_one_persisted_recovery_is_visible_and_later_pending_reset_wins(tmp_path):
    engine, sessions, scheduler = _scheduler(tmp_path)
    try:
        later = datetime.now(UTC) + timedelta(hours=4)
        scheduler._schedule_recovery(1, RecoveryReset(later, "test", {}), "usage_limit_error", None)
        earlier = datetime.now(UTC) + timedelta(hours=1)
        scheduler._schedule_recovery(1, RecoveryReset(earlier, "test", {}), "skipped_active_window", None)
        jobs = scheduler.next_runs()
        recovery = next(job for job in jobs if job.kind == RECOVERY_KIND)
        assert recovery.next_run_at is not None
        with sessions() as session:
            pending = session.get(DailyAnchorRecovery, 1)
            assert pending is not None
            assert pending.due_at.replace(tzinfo=UTC) >= later + timedelta(minutes=1)
        assert recovery.next_run_at.replace(tzinfo=UTC) >= later + timedelta(minutes=1)
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_usage_limit_failure_keeps_failed_run_and_persists_one_recovery(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'quota.sqlite3'}", data_dir=tmp_path)
    run_migrations(settings)
    engine = create_database_engine(settings)
    initialize_database(engine)
    sessions = create_session_factory(engine)
    now = datetime.now(UTC)
    with sessions() as session:
        seed_defaults(session, settings)
        account = session.get(Account, 1)
        assert account is not None
        account.auth_status = "connected"
        raw = UsageRaw(account_id=1, captured_at=now, parser_version="test", payload_json=json.dumps(_payload(five_reset=now + timedelta(hours=3), weekly_reset=now + timedelta(days=2))))
        session.add(raw)
        session.flush()
        snapshot = UsageSnapshot(account_id=1, captured_at=now, raw_usage_id=raw.id, parser_status="ok", source="test")
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        session.expunge(snapshot)
    anchors = AnchorService(sessions, settings, lambda: _QuotaBackend())
    scheduler = QuotaScheduler(sessions, _AnchorServiceRunner(anchors), settings, _Telemetry(snapshot))
    scheduler.start()
    try:
        with pytest.raises(RuntimeError, match="usage limit"):
            scheduler.run_anchor_job(1, "daily")
        with sessions() as session:
            assert session.query(AnchorRun).one().status == "failed"
            recovery = session.get(DailyAnchorRecovery, 1)
            assert recovery is not None
            assert recovery.originating_anchor_run_id == session.query(AnchorRun).one().id
            assert recovery.reset_source == "fresh-telemetry"
        assert any(job.kind == RECOVERY_KIND for job in scheduler.next_runs())
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_reload_and_new_scheduler_restore_one_pending_recovery(tmp_path):
    engine, sessions, scheduler = _scheduler(tmp_path)
    try:
        reset = datetime.now(UTC) + timedelta(hours=2)
        scheduler._schedule_recovery(1, RecoveryReset(reset, "test", {}), "test", None)
        scheduler.reload()
        assert len([job for job in scheduler.next_runs() if job.kind == RECOVERY_KIND]) == 1
        scheduler.shutdown()
        restored = QuotaScheduler(sessions, _Runner(), scheduler._settings)
        restored.start()
        try:
            assert len([job for job in restored.next_runs() if job.kind == RECOVERY_KIND]) == 1
        finally:
            restored.shutdown()
    finally:
        engine.dispose()


def test_execution_rechecks_pause_and_does_not_revive_pending_recovery(tmp_path):
    engine, sessions, scheduler = _scheduler(tmp_path)
    try:
        scheduler._schedule_recovery(
            1,
            RecoveryReset(datetime.now(UTC) + timedelta(hours=2), "test", {}),
            "test",
            None,
        )
        with sessions() as session:
            account = session.get(Account, 1)
            assert account is not None and account.schedule is not None
            account.schedule.anchor_paused = True
            generation = session.get(DailyAnchorRecovery, 1).generation
            session.commit()
        scheduler.run_recovery_job(1, generation)
        with sessions() as session:
            assert session.get(DailyAnchorRecovery, 1) is None
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_recovery_eligibility_honors_lifecycle_auth_and_due_weekday(tmp_path):
    engine, sessions, scheduler = _scheduler(tmp_path)
    try:
        with sessions() as session:
            account = session.get(Account, 1)
            assert account is not None and account.schedule is not None
            due = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)  # Wednesday
            assert _eligible_recovery_now(account, due)
            account.enabled = False
            assert not _eligible_recovery_now(account, due)
            account.enabled = True
            account.schedule.daily_anchor_enabled = False
            assert not _eligible_recovery_now(account, due)
            account.schedule.daily_anchor_enabled = True
            account.schedule.anchor_paused = True
            assert not _eligible_recovery_now(account, due)
            account.schedule.anchor_paused = False
            account.auth_status = "not_configured"
            assert not _eligible_recovery_now(account, due)
            account.auth_status = "connected"
            account.schedule.wednesday_enabled = False
            assert not _eligible_recovery_now(account, due)
    finally:
        scheduler.shutdown()
        engine.dispose()


class _BusyRunner:
    def run_scheduled_anchor(self, account_id, kind):
        from ai_quota_monitor.services.anchors import AnchorAlreadyRunningError
        raise AnchorAlreadyRunningError("busy")


class _CompletedRunner:
    def __init__(self, run_id, status="completed"):
        self.run_id = run_id
        self.status = status
        self.calls = 0

    def run_scheduled_anchor(self, account_id, kind):
        self.calls += 1
        return SmartAnchorResult(
            account_id=account_id,
            kind=kind,
            decision="sent",
            reason="test",
            verification_status="verified",
            anchor_run_id=self.run_id,
        )


class _ActiveSkipRunner:
    def __init__(self, snapshot_id, reset_at):
        self.snapshot_id = snapshot_id
        self.reset_at = reset_at
        self.calls = 0

    def run_scheduled_anchor(self, account_id, kind):
        self.calls += 1
        return SmartAnchorResult(
            account_id=account_id,
            kind=kind,
            decision="skipped_active_window",
            reason="active",
            verification_status="skipped",
            pre_snapshot_id=self.snapshot_id,
            observed_reset_before=self.reset_at,
        )


class _FixedRaceRunner:
    def __init__(self, scheduler, recovery_generation, run_id):
        self.scheduler = scheduler
        self.recovery_generation = recovery_generation
        self.run_id = run_id
        self.calls = 0

    def run_scheduled_anchor(self, account_id, kind):
        self.calls += 1
        if self.calls == 1:
            self.scheduler.run_recovery_job(account_id, self.recovery_generation)
        return SmartAnchorResult(
            account_id=account_id,
            kind=kind,
            decision="sent",
            reason="fixed success",
            verification_status="verified",
            anchor_run_id=self.run_id,
        )


def test_recovery_collision_has_one_bounded_delay_then_cancels(tmp_path):
    engine, sessions, scheduler = _scheduler(tmp_path)
    scheduler._anchor_runner = _BusyRunner()
    try:
        scheduler._schedule_recovery(1, RecoveryReset(datetime.now(UTC) + timedelta(hours=2), "test", {}), "test", None)
        with sessions() as session:
            first = session.get(DailyAnchorRecovery, 1)
            assert first is not None
            first_generation = first.generation
        scheduler.run_recovery_job(1, first_generation)
        with sessions() as session:
            delayed = session.get(DailyAnchorRecovery, 1)
            assert delayed is not None and delayed.collision_count == 1
            delayed_generation = delayed.generation
        scheduler.run_recovery_job(1, delayed_generation)
        with sessions() as session:
            assert session.get(DailyAnchorRecovery, 1) is None
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_stale_generation_cannot_clear_or_run_delete_recreate_recovery(tmp_path):
    engine, sessions, scheduler = _scheduler(tmp_path)
    try:
        first_reset = datetime.now(UTC) + timedelta(hours=2)
        scheduler._schedule_recovery(1, RecoveryReset(first_reset, "test", {}), "test", None)
        with sessions() as session:
            old_generation = session.get(DailyAnchorRecovery, 1).generation
        scheduler._clear_recovery(1, generation=old_generation, reason="test")
        scheduler._schedule_recovery(1, RecoveryReset(first_reset + timedelta(hours=1), "test", {}), "test", None)
        with sessions() as session:
            replacement = session.get(DailyAnchorRecovery, 1)
            assert replacement is not None
            new_generation = replacement.generation
        scheduler._clear_recovery(1, generation=old_generation, reason="stale")
        scheduler.run_recovery_job(1, old_generation)
        with sessions() as session:
            assert session.get(DailyAnchorRecovery, 1).generation == new_generation
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_active_preflight_skip_persists_reset_recovery_without_anchor_turn(tmp_path):
    engine, sessions, scheduler = _scheduler(tmp_path)
    now = datetime.now(UTC)
    reset_at = now + timedelta(hours=2)
    try:
        with sessions() as session:
            raw = UsageRaw(account_id=1, captured_at=now, parser_version="test", payload_json=json.dumps(_payload(five_used=30, five_reset=reset_at, weekly_reset=now + timedelta(days=2))))
            session.add(raw)
            session.flush()
            snapshot = UsageSnapshot(account_id=1, captured_at=now, raw_usage_id=raw.id, parser_status="ok", source="test")
            session.add(snapshot)
            session.commit()
            snapshot_id = snapshot.id
        runner = _ActiveSkipRunner(snapshot_id, reset_at)
        scheduler._anchor_runner = runner
        scheduler.run_anchor_job(1, "daily")
        with sessions() as session:
            recovery = session.get(DailyAnchorRecovery, 1)
            assert recovery is not None
            assert recovery.reset_source == "fresh-active-window-telemetry"
            assert recovery.due_at.replace(tzinfo=UTC) == reset_at.replace(microsecond=0) + timedelta(minutes=1)
        assert runner.calls == 1
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_fixed_success_between_recovery_claim_and_execution_prevents_second_turn(tmp_path):
    engine, sessions, scheduler = _scheduler(tmp_path)
    try:
        with sessions() as session:
            run = AnchorRun(account_id=1, status="completed", prompt="OK", started_at=datetime.now(UTC))
            session.add(run)
            session.commit()
            run_id = run.id
        scheduler._schedule_recovery(1, RecoveryReset(datetime.now(UTC) + timedelta(hours=2), "test", {}), "test", None)
        with sessions() as session:
            generation = session.get(DailyAnchorRecovery, 1).generation
        runner = _FixedRaceRunner(scheduler, generation, run_id)
        scheduler._anchor_runner = runner
        scheduler.run_anchor_job(1, "daily")
        with sessions() as session:
            assert session.get(DailyAnchorRecovery, 1) is None
        assert runner.calls == 1
    finally:
        scheduler.shutdown()
        engine.dispose()


def test_quota_blocked_recovery_replaces_its_generation_with_later_reset(tmp_path):
    engine, sessions, scheduler = _scheduler(tmp_path)
    now = datetime.now(UTC)
    try:
        with sessions() as session:
            raw = UsageRaw(account_id=1, captured_at=now, parser_version="test", payload_json=json.dumps(_payload(five_reset=now + timedelta(hours=5), weekly_reset=now + timedelta(days=2))))
            session.add(raw)
            session.flush()
            snapshot = UsageSnapshot(account_id=1, captured_at=now, raw_usage_id=raw.id, parser_status="ok", source="test")
            session.add(snapshot)
            session.commit()
            session.refresh(snapshot)
            session.expunge(snapshot)
        scheduler._telemetry_refresher = _Telemetry(snapshot)
        scheduler._anchor_runner = _UsageLimitRunner()
        scheduler._schedule_recovery(1, RecoveryReset(now + timedelta(hours=1), "test", {}), "test", None)
        with sessions() as session:
            old_generation = session.get(DailyAnchorRecovery, 1).generation
        scheduler.run_recovery_job(1, old_generation)
        with sessions() as session:
            replacement = session.get(DailyAnchorRecovery, 1)
            assert replacement is not None
            assert replacement.generation != old_generation
            assert replacement.due_at.replace(tzinfo=UTC) > (
                now + timedelta(hours=5)
            ).replace(microsecond=0)
        assert any(job.id.endswith(replacement.generation) for job in scheduler._scheduler.get_jobs())
    finally:
        scheduler.shutdown()
        engine.dispose()


class _FrozenDatetime(datetime):
    current = datetime(2026, 10, 7, 15, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz is None else cls.current.astimezone(tz)


@pytest.mark.parametrize("policy,should_keep", [("run_if_within_grace", True), ("skip_missed", False)])
def test_overdue_recovery_honors_policy_with_controlled_clock(tmp_path, monkeypatch, policy, should_keep):
    monkeypatch.setattr(scheduler_module, "datetime", _FrozenDatetime)
    engine, sessions, scheduler = _scheduler(tmp_path)
    try:
        scheduler._scheduler.pause()
        with sessions() as session:
            update_app_setting(session, "missed_anchor_policy", policy)
            session.add(DailyAnchorRecovery(
                account_id=1, origin_decision="test", reset_source="test",
                reset_at=_FrozenDatetime.current - timedelta(minutes=2),
                due_at=_FrozenDatetime.current - timedelta(seconds=20),
                generation="overdue", version=1, collision_count=0,
            ))
            session.commit()
        scheduler._restore_recoveries(scheduler._runtime_config())
        with sessions() as session:
            pending = session.get(DailyAnchorRecovery, 1)
            assert (pending is not None) is should_keep
        if should_keep:
            assert any(job.kind == RECOVERY_KIND for job in scheduler.next_runs())
    finally:
        scheduler.shutdown()
        engine.dispose()


@pytest.mark.parametrize("mutation", ["disabled", "archived", "paused", "daily_disabled", "disconnected", "weekday_disabled"])
def test_reload_and_execution_cancel_lifecycle_ineligible_recovery(tmp_path, mutation):
    engine, sessions, scheduler = _scheduler(tmp_path)
    try:
        scheduler._schedule_recovery(1, RecoveryReset(datetime.now(UTC) + timedelta(hours=2), "test", {}), "test", None)
        with sessions() as session:
            account = session.get(Account, 1)
            assert account is not None and account.schedule is not None
            generation = session.get(DailyAnchorRecovery, 1).generation
            if mutation == "disabled":
                account.enabled = False
            elif mutation == "archived":
                account.archived_at = datetime.now(UTC)
            elif mutation == "paused":
                account.schedule.anchor_paused = True
            elif mutation == "daily_disabled":
                account.schedule.daily_anchor_enabled = False
            elif mutation == "disconnected":
                account.auth_status = "not_configured"
            else:
                weekday = datetime.now(UTC).astimezone(
                    ZoneInfo(account.schedule.timezone)
                ).strftime("%A").lower()
                setattr(account.schedule, f"{weekday}_enabled", False)
            session.commit()
        scheduler.reload()
        scheduler.run_recovery_job(1, generation)
        with sessions() as session:
            assert session.get(DailyAnchorRecovery, 1) is None
    finally:
        scheduler.shutdown()
        engine.dispose()


@pytest.mark.parametrize("status,completed", [("completed", True), ("failed", False), ("interrupted", False)])
def test_recovery_only_treats_completed_anchor_run_as_success(tmp_path, status, completed):
    engine, sessions, scheduler = _scheduler(tmp_path)
    try:
        with sessions() as session:
            run = AnchorRun(account_id=1, status=status, prompt="OK", started_at=datetime.now(UTC))
            session.add(run)
            session.commit()
            run_id = run.id
        runner = _CompletedRunner(run_id, status)
        scheduler._anchor_runner = runner
        scheduler._schedule_recovery(1, RecoveryReset(datetime.now(UTC) + timedelta(hours=2), "test", {}), "test", None)
        with sessions() as session:
            generation = session.get(DailyAnchorRecovery, 1).generation
        scheduler.run_recovery_job(1, generation)
        with sessions() as session:
            assert session.get(DailyAnchorRecovery, 1) is None
            messages = [event.message for event in session.query(EventLog).all()]
        if completed:
            assert not any(job.kind == RECOVERY_KIND for job in scheduler.next_runs())
            assert any("recovery completed" in message for message in messages)
        else:
            # Non-completed returned states are cancelled, never recorded as completed.
            assert any("returned no completed anchor" in message for message in messages)
            assert not any("recovery completed" in message for message in messages)
    finally:
        scheduler.shutdown()
        engine.dispose()
