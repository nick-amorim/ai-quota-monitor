from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from functools import wraps
import threading
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from ai_quota_monitor.config import Settings
from ai_quota_monitor.models import Account, AnchorRun, AppSetting, DailyAnchorRecovery, UsageRaw
from ai_quota_monitor.services.accounts import WEEKDAYS, list_accounts
from ai_quota_monitor.services.anchors import AnchorAlreadyRunningError
from ai_quota_monitor.services.events import record_event
from ai_quota_monitor.services.reset_times import FIVE_HOUR_WINDOW_MINUTES
from ai_quota_monitor.services.recovery import (
    RecoveryReset,
    fresh_active_window_reset,
    fresh_blocking_reset,
    is_usage_limit_error,
    parse_usage_limit_reset,
    structured_reset_from_error,
)
from ai_quota_monitor.services.smart_anchors import SmartAnchorResult

SCHEDULER_JOB_PREFIX = "anchor:"
TELEMETRY_JOB_ID = "telemetry:refresh-all"
RECOVERY_KIND = "daily_recovery"
COLLISION_DELAY = timedelta(minutes=1)
AP_DAYS = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}


def _recovery_synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        account_id = kwargs.get("account_id", args[0] if args else None)
        if not isinstance(account_id, int):
            return method(self, *args, **kwargs)
        with self._account_recovery_lock(account_id):
            return method(self, *args, **kwargs)
    return wrapped


@dataclass(frozen=True)
class ScheduledAnchorJob:
    id: str
    account_id: int
    account_name: str
    kind: str
    next_run_at: datetime | None
    timezone: str
    sequence: int | None = None


@dataclass(frozen=True)
class SchedulerRuntimeConfig:
    missed_anchor_policy: str
    misfire_grace_time: int


class SchedulerService(Protocol):
    @property
    def running(self) -> bool:
        ...

    def start(self) -> None:
        ...

    def shutdown(self) -> None:
        ...

    def reload(self) -> None:
        ...

    def next_runs(self) -> list[ScheduledAnchorJob]:
        ...


class ScheduledAnchorRunner(Protocol):
    def run_scheduled_anchor(self, account_id: int, kind: str) -> SmartAnchorResult:
        ...


class UsageTelemetryRefresher(Protocol):
    def refresh_connected_accounts(self) -> object:
        ...

    def refresh_account_usage(self, account_id: int) -> object:
        ...


class QuotaScheduler:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        anchor_runner: ScheduledAnchorRunner,
        settings: Settings,
        telemetry_refresher: UsageTelemetryRefresher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._anchor_runner = anchor_runner
        self._settings = settings
        self._telemetry_refresher = telemetry_refresher
        self._scheduler: BackgroundScheduler | None = None
        self._recovery_locks: dict[int, threading.RLock] = {}
        self._recovery_locks_guard = threading.Lock()
        self._inflight_recoveries: set[tuple[int, str]] = set()
        self._execution_locks: dict[int, threading.Lock] = {}
        self._execution_locks_guard = threading.Lock()

    @property
    def running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def start(self) -> None:
        if self.running:
            return

        self._scheduler = BackgroundScheduler(
            timezone=_timezone(self._settings.timezone),
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": _grace_seconds(
                    self._settings.missed_anchor_grace_minutes
                ),
            },
        )
        self._scheduler.add_listener(
            self._record_apscheduler_event,
            EVENT_JOB_ERROR | EVENT_JOB_MISSED,
        )
        self._scheduler.start()
        self.reload()
        self._record_event(
            level="info",
            category="scheduler",
            message="Scheduler started",
        )

    def shutdown(self) -> None:
        if self._scheduler is None:
            return

        try:
            self._scheduler.shutdown(wait=False)
        except SchedulerNotRunningError:
            pass
        finally:
            self._scheduler = None

    def reload(self) -> None:
        if self._scheduler is None:
            return

        scheduler = self._require_scheduler()
        self._remove_fixed_anchor_jobs()
        self._sync_telemetry_job()
        runtime_config = self._runtime_config()

        added = 0
        with self._session_factory() as session:
            accounts = list_accounts(session)

        for account in accounts:
            if (
                not account.enabled
                or account.schedule is None
                or account.schedule.anchor_paused
            ):
                continue

            if account.schedule.daily_anchor_enabled:
                active_days = _active_weekdays(account)
                if active_days:
                    for sequence, anchor_time in enumerate(
                        daily_anchor_times(account.schedule.daily_anchor_time)
                    ):
                        scheduler.add_job(
                            self.run_anchor_job,
                            trigger=CronTrigger(
                                day_of_week=",".join(active_days),
                                hour=anchor_time.hour,
                                minute=anchor_time.minute,
                                timezone=_timezone(account.schedule.timezone),
                            ),
                            args=(account.id, "daily"),
                            id=_job_id(account.id, "daily", sequence=sequence),
                            misfire_grace_time=runtime_config.misfire_grace_time,
                            name=f"{account.name} daily anchor {anchor_time:%H:%M}",
                            replace_existing=True,
                        )
                        added += 1

        self._restore_recoveries(runtime_config)

        self._record_event(
            level="info",
            category="scheduler",
            message="Scheduler jobs reloaded",
            payload={
                "jobs": added,
                "missed_anchor_policy": runtime_config.missed_anchor_policy,
                "misfire_grace_time": runtime_config.misfire_grace_time,
            },
        )

    def next_runs(self) -> list[ScheduledAnchorJob]:
        if self._scheduler is None:
            return []

        account_names = self._account_names()
        account_timezones = self._account_timezones()
        jobs: list[ScheduledAnchorJob] = []
        for job in self._scheduler.get_jobs():
            if not job.id.startswith(SCHEDULER_JOB_PREFIX):
                continue

            account_id, kind, sequence = _parse_job_id(job.id)
            jobs.append(
                ScheduledAnchorJob(
                    id=job.id,
                    account_id=account_id,
                    account_name=account_names.get(account_id, f"Account {account_id}"),
                    kind=kind,
                    sequence=sequence,
                    next_run_at=job.next_run_time,
                    timezone=account_timezones.get(
                        account_id,
                        str(
                            getattr(job.trigger, "timezone", None)
                            or getattr(job.next_run_time, "tzinfo", "UTC")
                        ),
                    ),
                )
            )

        return sorted(
            jobs,
            key=lambda item: (
                item.next_run_at is None,
                item.next_run_at or datetime.max,
                item.account_id,
                item.kind,
                item.sequence if item.sequence is not None else -1,
            ),
        )

    def run_anchor_job(self, account_id: int, kind: str) -> None:
        execution_lock = self._account_execution_lock(account_id)
        if not execution_lock.acquire(blocking=False):
            self._record_event(
                level="warning",
                category="scheduler.anchor",
                message=f"Scheduled {kind} anchor skipped because another scheduled run is active",
                account_id=account_id,
            )
            return
        try:
            self._record_event(
                level="info",
                category="scheduler.anchor",
                message=f"Scheduled {kind} anchor started",
                account_id=account_id,
            )
            try:
                result = self._anchor_runner.run_scheduled_anchor(account_id, kind)
            except AnchorAlreadyRunningError as exc:
                self._record_event(
                    level="warning", category="scheduler.anchor",
                    message=f"Scheduled {kind} anchor skipped because another run is active",
                    account_id=account_id, payload={"error": str(exc)},
                )
            except Exception as exc:
                self._record_event(
                    level="error", category="scheduler.anchor", message=f"Scheduled {kind} anchor failed",
                    account_id=account_id, payload={"error": str(exc)},
                )
                if kind == "daily" and is_usage_limit_error(exc):
                    self._recover_after_usage_limit(account_id, exc)
                raise
            else:
                verb = "skipped" if result.decision.startswith("skipped") else "completed"
                level = "info" if result.decision.startswith("skipped") or result.verification_status == "verified" else "warning"
                self._record_event(
                    level=level, category="scheduler.anchor", message=f"Scheduled {kind} anchor {verb}",
                    account_id=account_id,
                    payload={"decision": result.decision, "verification_status": result.verification_status,
                             "anchor_run_id": result.anchor_run_id},
                )
                if kind == "daily" and result.decision == "skipped_active_window":
                    self._recover_after_active_window(account_id, result)
                elif kind == "daily" and self._completed_anchor(result):
                    self._clear_recovery(account_id, reason="a fixed daily anchor completed")
        finally:
            execution_lock.release()

    def run_usage_refresh_job(self) -> None:
        if self._telemetry_refresher is None:
            return
        try:
            self._telemetry_refresher.refresh_connected_accounts()
        except Exception as exc:
            self._record_event(
                level="error",
                category="scheduler.telemetry",
                message="Scheduled usage refresh failed",
                payload={"error": str(exc)},
            )
            raise

    def run_recovery_job(self, account_id: int, generation: str) -> None:
        recovery = self._claim_recovery(account_id, generation)
        if recovery is None:
            return
        execution_lock: threading.Lock | None = None
        try:
            execution_lock = self._account_execution_lock(account_id)
            if not execution_lock.acquire(blocking=False):
                self._delay_recovery(
                    account_id,
                    generation,
                    "another scheduled anchor is active for this account",
                )
                execution_lock = None
                return
            # A fixed job can have cleared or replaced this row while this recovery was
            # waiting for the per-account scheduled-run guard.
            account = self._load_account(account_id)
            if (
                self._current_recovery(account_id, generation) is None
                or account is None
                or not _eligible_recovery_now(account, datetime.now(UTC))
            ):
                self._clear_recovery(
                    account_id,
                    generation=generation,
                    reason="recovery is no longer eligible",
                    cancelled=True,
                )
                return
            self._record_event(
                level="info", category="scheduler.recovery", message="Daily anchor recovery started",
                account_id=account_id, payload=_recovery_payload(recovery),
            )
            try:
                result = self._anchor_runner.run_scheduled_anchor(account_id, "daily")
            except AnchorAlreadyRunningError as exc:
                self._delay_recovery(account_id, generation, str(exc))
            except Exception as exc:
                self._record_event(
                    level="error", category="scheduler.recovery", message="Daily anchor recovery failed",
                    account_id=account_id, payload={**_recovery_payload(recovery), "error": str(exc)},
                )
                if is_usage_limit_error(exc):
                    self._recover_after_usage_limit(account_id, exc, replacing_generation=generation)
                else:
                    self._clear_recovery(
                        account_id, generation=generation,
                        reason="recovery failed with a non-quota error", cancelled=True,
                    )
            else:
                if result.decision == "skipped_active_window":
                    self._recover_after_active_window(account_id, result, replacing_generation=generation)
                elif self._completed_anchor(result):
                    self._clear_recovery(account_id, generation=generation, reason="recovery completed")
                else:
                    self._clear_recovery(
                        account_id, generation=generation,
                        reason="recovery returned no completed anchor", cancelled=True,
                    )
        finally:
            if execution_lock is not None:
                execution_lock.release()
            self._release_recovery_claim(account_id, generation)

    def _recover_after_active_window(
        self, account_id: int, result: SmartAnchorResult, *, replacing_generation: str | None = None
    ) -> None:
        reset_at = _as_aware_utc(result.observed_reset_before)
        if reset_at is None or reset_at <= datetime.now(UTC):
            self._record_unresolved(account_id, "active-window skip did not include a future observed reset", result.anchor_run_id)
            self._settle_unresolved_replacement(account_id, replacing_generation)
            return
        # SmartAnchorService's pre-refresh is a direct app-server read.  Its snapshot points to
        # that raw payload, so confirm the 5-hour reset there rather than trusting inherited fields.
        snapshot, raw = self._snapshot_and_raw(result.pre_snapshot_id)
        fresh = fresh_active_window_reset(snapshot, raw, now=datetime.now(UTC))
        if fresh is None:
            self._record_unresolved(account_id, "active-window reset was not present in fresh telemetry", result.anchor_run_id)
            self._settle_unresolved_replacement(account_id, replacing_generation)
            return
        self._schedule_recovery(
            account_id, fresh, "skipped_active_window", result.anchor_run_id,
            replacing_generation=replacing_generation,
        )

    def _recover_after_usage_limit(
        self, account_id: int, exc: Exception, *, replacing_generation: str | None = None
    ) -> None:
        failed_at = datetime.now(UTC)
        resolved = structured_reset_from_error(exc, now=failed_at)
        snapshot = None
        if resolved is None and self._telemetry_refresher is not None:
            try:
                refreshed = self._telemetry_refresher.refresh_account_usage(account_id)
                snapshot = getattr(refreshed, "snapshot", None)
            except Exception as refresh_exc:
                self._record_event(
                    level="warning", category="scheduler.recovery", message="Recovery telemetry refresh failed",
                    account_id=account_id, payload={"error": str(refresh_exc)},
                )
        raw = self._raw_for_snapshot(snapshot)
        if resolved is None:
            resolved = fresh_blocking_reset(snapshot, raw, now=failed_at)
        account = self._load_account(account_id)
        if resolved is None and account is not None and account.schedule is not None:
            resolved = parse_usage_limit_reset(
                str(exc), failed_at=failed_at, timezone_name=account.schedule.timezone
            )
        run_id = self._latest_failed_run_id(account_id)
        if resolved is None:
            self._record_unresolved(account_id, "quota failure had no trustworthy future reset", run_id)
            if replacing_generation is not None:
                self._clear_recovery(
                    account_id, generation=replacing_generation,
                    reason="recovery had no trustworthy replacement reset", cancelled=True,
                )
            return
        self._schedule_recovery(
            account_id, resolved, "usage_limit_error", run_id,
            replacing_generation=replacing_generation,
        )

    @_recovery_synchronized
    def _schedule_recovery(
        self, account_id: int, reset: RecoveryReset, origin: str, run_id: int | None,
        *, replacing_generation: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        reset_at = _as_aware_utc(reset.reset_at)
        assert reset_at is not None
        due_at = reset_at + timedelta(minutes=1)
        if due_at <= now:
            self._record_unresolved(account_id, "resolved reset is no longer in the future", run_id)
            self._settle_unresolved_replacement(account_id, replacing_generation)
            return
        account = self._load_account(account_id)
        if account is None or not _eligible_recovery_now(account, due_at):
            self._record_unresolved(account_id, "recovery is no longer eligible at its due time", run_id)
            self._settle_unresolved_replacement(account_id, replacing_generation)
            return
        with self._session_factory() as session:
            existing = session.get(DailyAnchorRecovery, account_id)
            if replacing_generation is not None and (
                existing is None or existing.generation != replacing_generation
            ):
                return
            # A weekly limit can require a later retry than a later 5-hour preflight skip.
            if existing is not None and _as_aware_utc(existing.due_at) >= due_at:
                persisted = existing
                action = "retained"
            elif existing is None:
                persisted = DailyAnchorRecovery(
                    account_id=account_id, originating_anchor_run_id=run_id,
                    origin_decision=origin, reset_source=reset.source, reset_at=reset_at,
                    due_at=due_at, generation=uuid4().hex, version=1, collision_count=0,
                )
                session.add(persisted)
                action = "scheduled"
            else:
                persisted = existing
                persisted.originating_anchor_run_id = run_id
                persisted.origin_decision = origin
                persisted.reset_source = reset.source
                persisted.reset_at = reset_at
                persisted.due_at = due_at
                persisted.generation = uuid4().hex
                persisted.version += 1
                persisted.collision_count = 0
                action = "rescheduled"
            session.commit()
            session.refresh(persisted)
            session.expunge(persisted)
        if action != "retained":
            self._add_recovery_job(persisted)
        self._record_event(
            level="info", category="scheduler.recovery", message=f"Daily anchor recovery {action}",
            account_id=account_id,
            payload={**_recovery_payload(persisted), **reset.detail,
                     "originating_anchor_run_id": run_id, "origin_decision": origin},
        )

    def _restore_recoveries(self, runtime_config: SchedulerRuntimeConfig) -> None:
        now = datetime.now(UTC)
        with self._session_factory() as session:
            recoveries = list(session.scalars(select(DailyAnchorRecovery)))
            for recovery in recoveries:
                session.expunge(recovery)
        for recovery in recoveries:
            with self._account_recovery_lock(recovery.account_id):
                current = self._current_recovery(recovery.account_id, recovery.generation)
                if current is None:
                    continue
                account = self._load_account(current.account_id)
                if account is None or not _eligible_recovery_now(account, _as_aware_utc(current.due_at)):
                    self._clear_recovery(current.account_id, generation=current.generation, reason="recovery is no longer eligible", cancelled=True)
                    continue
                due_at = _as_aware_utc(current.due_at)
                assert due_at is not None
                if due_at < now:
                    if runtime_config.missed_anchor_policy == "skip_missed" or (now - due_at).total_seconds() > runtime_config.misfire_grace_time:
                        self._clear_recovery(current.account_id, generation=current.generation, reason="recovery missed outside grace window", cancelled=True, missed=True)
                        continue
                    current = self._make_recovery_due_now(current.account_id, current.generation) or current
                self._add_recovery_job(current)

    def _make_recovery_due_now(self, account_id: int, generation: str) -> DailyAnchorRecovery | None:
        with self._session_factory() as session:
            recovery = session.get(DailyAnchorRecovery, account_id)
            if recovery is None or recovery.generation != generation:
                return None
            recovery.due_at = datetime.now(UTC) + timedelta(seconds=1)
            recovery.generation = uuid4().hex
            recovery.version += 1
            session.commit()
            session.refresh(recovery)
            session.expunge(recovery)
            return recovery

    def _add_recovery_job(self, recovery: DailyAnchorRecovery) -> None:
        scheduler = self._require_scheduler()
        for job in scheduler.get_jobs():
            if job.id.startswith(SCHEDULER_JOB_PREFIX) and _parse_job_id(job.id)[:2] == (
                recovery.account_id,
                RECOVERY_KIND,
            ):
                scheduler.remove_job(job.id)
        scheduler.add_job(
            self.run_recovery_job,
            trigger=DateTrigger(run_date=_as_aware_utc(recovery.due_at)),
            args=(recovery.account_id, recovery.generation),
            id=_recovery_job_id(recovery.account_id, recovery.generation),
            misfire_grace_time=self._runtime_config().misfire_grace_time,
            name=f"Account {recovery.account_id} daily recovery",
            replace_existing=True,
        )

    @_recovery_synchronized
    def _delay_recovery(self, account_id: int, generation: str, error: str) -> None:
        with self._session_factory() as session:
            recovery = session.get(DailyAnchorRecovery, account_id)
            if recovery is None or recovery.generation != generation:
                return
            if recovery.collision_count >= 1:
                session.delete(recovery)
                session.commit()
                self._record_event(level="warning", category="scheduler.recovery", message="Daily anchor recovery cancelled after collision", account_id=account_id, payload={"error": error})
                return
            recovery.collision_count += 1
            recovery.due_at = datetime.now(UTC) + COLLISION_DELAY
            recovery.generation = uuid4().hex
            recovery.version += 1
            session.commit()
            session.refresh(recovery)
            session.expunge(recovery)
        self._add_recovery_job(recovery)
        self._record_event(level="warning", category="scheduler.recovery", message="Daily anchor recovery delayed after collision", account_id=account_id, payload={**_recovery_payload(recovery), "error": error})

    @_recovery_synchronized
    def _clear_recovery(self, account_id: int, *, reason: str, generation: str | None = None, cancelled: bool = False, missed: bool = False) -> None:
        with self._session_factory() as session:
            recovery = session.get(DailyAnchorRecovery, account_id)
            if recovery is None or (generation is not None and recovery.generation != generation):
                return
            payload = _recovery_payload(recovery)
            session.delete(recovery)
            session.commit()
        if self._scheduler is not None:
            job = self._scheduler.get_job(
                _recovery_job_id(account_id, generation if generation is not None else payload["generation"])
            )
            if job is not None:
                self._scheduler.remove_job(job.id)
        action = "missed" if missed else "cancelled" if cancelled else "completed"
        self._record_event(level="warning" if cancelled or missed else "info", category="scheduler.missed" if missed else "scheduler.recovery", message=f"Daily anchor recovery {action}: {reason}", account_id=account_id, payload=payload)

    def _current_recovery(self, account_id: int, generation: str | None = None) -> DailyAnchorRecovery | None:
        with self._session_factory() as session:
            recovery = session.get(DailyAnchorRecovery, account_id)
            if recovery is None or (generation is not None and recovery.generation != generation):
                return None
            session.expunge(recovery)
            return recovery

    def _settle_unresolved_replacement(
        self, account_id: int, generation: str | None
    ) -> None:
        if generation is not None:
            self._clear_recovery(
                account_id,
                generation=generation,
                reason="recovery had no usable replacement reset",
                cancelled=True,
            )

    def _account_recovery_lock(self, account_id: int) -> threading.RLock:
        with self._recovery_locks_guard:
            lock = self._recovery_locks.get(account_id)
            if lock is None:
                lock = threading.RLock()
                self._recovery_locks[account_id] = lock
            return lock

    def _account_execution_lock(self, account_id: int) -> threading.Lock:
        with self._execution_locks_guard:
            lock = self._execution_locks.get(account_id)
            if lock is None:
                lock = threading.Lock()
                self._execution_locks[account_id] = lock
            return lock

    def _claim_recovery(self, account_id: int, generation: str) -> DailyAnchorRecovery | None:
        with self._account_recovery_lock(account_id):
            key = (account_id, generation)
            if key in self._inflight_recoveries:
                return None
            recovery = self._current_recovery(account_id, generation)
            if recovery is None:
                return None
            self._inflight_recoveries.add(key)
            return recovery

    def _release_recovery_claim(self, account_id: int, generation: str) -> None:
        with self._account_recovery_lock(account_id):
            self._inflight_recoveries.discard((account_id, generation))

    def _load_account(self, account_id: int) -> Account | None:
        with self._session_factory() as session:
            account = session.scalar(select(Account).options(selectinload(Account.schedule)).where(Account.id == account_id, Account.archived_at.is_(None)))
            if account is not None:
                session.expunge(account)
            return account

    def _snapshot_and_raw(self, snapshot_id: int | None):
        if snapshot_id is None:
            return None, None
        from ai_quota_monitor.models import UsageSnapshot
        with self._session_factory() as session:
            snapshot = session.get(UsageSnapshot, snapshot_id)
            raw = session.get(UsageRaw, snapshot.raw_usage_id) if snapshot and snapshot.raw_usage_id else None
            return snapshot, raw

    def _raw_for_snapshot(self, snapshot) -> UsageRaw | None:
        if snapshot is None or snapshot.raw_usage_id is None:
            return None
        with self._session_factory() as session:
            return session.get(UsageRaw, snapshot.raw_usage_id)

    def _latest_failed_run_id(self, account_id: int) -> int | None:
        with self._session_factory() as session:
            return session.scalar(select(AnchorRun.id).where(AnchorRun.account_id == account_id, AnchorRun.status == "failed").order_by(AnchorRun.id.desc()).limit(1))

    def _completed_anchor(self, result: SmartAnchorResult) -> bool:
        if result.anchor_run_id is None or not result.decision.startswith("sent"):
            return False
        with self._session_factory() as session:
            run = session.get(AnchorRun, result.anchor_run_id)
            return run is not None and run.status == "completed"

    def _require_scheduler(self) -> BackgroundScheduler:
        if self._scheduler is None:
            raise RuntimeError("Scheduler has not been started")
        return self._scheduler

    def _remove_fixed_anchor_jobs(self) -> None:
        scheduler = self._require_scheduler()
        for job in scheduler.get_jobs():
            if job.id.startswith(SCHEDULER_JOB_PREFIX) and RECOVERY_KIND not in job.id:
                scheduler.remove_job(job.id)

    def _sync_telemetry_job(self) -> None:
        scheduler = self._require_scheduler()
        existing = scheduler.get_job(TELEMETRY_JOB_ID)
        if existing is not None:
            scheduler.remove_job(TELEMETRY_JOB_ID)

        if self._telemetry_refresher is None:
            return

        interval_minutes = self._usage_poll_interval_minutes()
        scheduler.add_job(
            self.run_usage_refresh_job,
            trigger=IntervalTrigger(
                minutes=interval_minutes,
                timezone=_timezone(self._settings.timezone),
            ),
            id=TELEMETRY_JOB_ID,
            max_instances=1,
            misfire_grace_time=max(30, interval_minutes * 60),
            name="Codex usage telemetry refresh",
            next_run_time=datetime.now(_timezone(self._settings.timezone)),
            replace_existing=True,
        )

    def _usage_poll_interval_minutes(self) -> int:
        with self._session_factory() as session:
            return max(
                1,
                _int_setting_value(
                    session,
                    "usage_poll_interval_minutes",
                    self._settings.usage_poll_interval_minutes,
                ),
            )

    def _record_apscheduler_event(self, event: JobExecutionEvent) -> None:
        if not event.job_id.startswith(SCHEDULER_JOB_PREFIX):
            return

        account_id, kind, _sequence = _parse_job_id(event.job_id)
        if event.code == EVENT_JOB_MISSED:
            if kind == RECOVERY_KIND:
                self._settle_missed_recovery(
                    account_id,
                    _recovery_generation_from_job_id(event.job_id),
                    event.scheduled_run_time,
                )
            self._record_event(
                level="warning",
                category="scheduler.missed",
                message=f"Missed {kind} anchor job outside grace window",
                account_id=account_id,
                payload={"scheduled_run_time": event.scheduled_run_time.isoformat()},
            )
        elif event.code == EVENT_JOB_ERROR:
            self._record_event(
                level="error",
                category="scheduler.error",
                message=f"Scheduler job {kind} raised an error",
                account_id=account_id,
                payload={"error": str(event.exception)},
            )

    @_recovery_synchronized
    def _settle_missed_recovery(
        self, account_id: int, generation: str | None, scheduled_run_time: datetime
    ) -> None:
        """Do not let a date job consumed as a misfire reappear after restart."""
        recovery = self._current_recovery(account_id, generation)
        if recovery is None:
            return
        due_at = _as_aware_utc(recovery.due_at)
        scheduled_at = _as_aware_utc(scheduled_run_time)
        if due_at is not None and scheduled_at is not None and due_at <= scheduled_at:
            self._clear_recovery(
                account_id, generation=recovery.generation,
                reason="recovery job missed outside grace window", cancelled=True, missed=True,
            )

    def _record_event(
        self,
        *,
        level: str,
        category: str,
        message: str,
        account_id: int | None = None,
        payload: dict | None = None,
    ) -> None:
        with self._session_factory() as session:
            record_event(
                session,
                level=level,
                category=category,
                message=message,
                account_id=account_id,
                payload=payload,
            )

    def _account_names(self) -> dict[int, str]:
        with self._session_factory() as session:
            return {account.id: account.name for account in session.query(Account).all()}

    def _account_timezones(self) -> dict[int, str]:
        with self._session_factory() as session:
            return {
                account.id: account.schedule.timezone
                for account in session.scalars(select(Account).options(selectinload(Account.schedule)))
                if account.schedule is not None
            }

    def _runtime_config(self) -> SchedulerRuntimeConfig:
        with self._session_factory() as session:
            policy = _setting_value(
                session,
                "missed_anchor_policy",
                self._settings.missed_anchor_policy,
            )
            grace_minutes = _int_setting_value(
                session,
                "missed_anchor_grace_minutes",
                self._settings.missed_anchor_grace_minutes,
            )

        if policy == "skip_missed":
            return SchedulerRuntimeConfig(
                missed_anchor_policy=policy,
                misfire_grace_time=1,
            )

        return SchedulerRuntimeConfig(
            missed_anchor_policy="run_if_within_grace",
            misfire_grace_time=_grace_seconds(grace_minutes),
        )


def _job_id(account_id: int, kind: str, *, sequence: int | None = None) -> str:
    suffix = f":{sequence}" if sequence is not None else ""
    return f"{SCHEDULER_JOB_PREFIX}{account_id}:{kind}{suffix}"


def _recovery_job_id(account_id: int, generation: str) -> str:
    return f"{SCHEDULER_JOB_PREFIX}{account_id}:{RECOVERY_KIND}:{generation}"


def _parse_job_id(job_id: str) -> tuple[int, str, int | None]:
    parts = job_id.split(":")
    if len(parts) < 3 or parts[0] != "anchor":
        raise ValueError(f"Invalid scheduler job id: {job_id}")

    sequence = int(parts[3]) if len(parts) > 3 and parts[2] != RECOVERY_KIND else None
    return int(parts[1]), parts[2], sequence


def _recovery_generation_from_job_id(job_id: str) -> str | None:
    parts = job_id.split(":")
    if len(parts) == 4 and parts[2] == RECOVERY_KIND:
        return parts[3]
    return None


def daily_anchor_times(first_anchor_time: time) -> tuple[time, ...]:
    current = datetime.combine(datetime.min.date(), first_anchor_time)
    day_end = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    anchors = []
    while current < day_end:
        anchors.append(current.time().replace(second=0, microsecond=0))
        current += timedelta(minutes=FIVE_HOUR_WINDOW_MINUTES)
    return tuple(anchors)


def _active_weekdays(account: Account) -> list[str]:
    assert account.schedule is not None
    return [
        AP_DAYS[weekday]
        for weekday in WEEKDAYS
        if getattr(account.schedule, f"{weekday}_enabled")
    ]


def _eligible_for_daily(account: Account) -> bool:
    return bool(
        account.enabled
        and account.schedule is not None
        and account.schedule.daily_anchor_enabled
        and not account.schedule.anchor_paused
    )


def _eligible_recovery_now(account: Account, now: datetime) -> bool:
    if not _eligible_for_daily(account) or account.auth_status != "connected":
        return False
    assert account.schedule is not None
    local_now = _as_aware_utc(now).astimezone(_timezone(account.schedule.timezone))
    weekday = WEEKDAYS[local_now.weekday()]
    return bool(getattr(account.schedule, f"{weekday}_enabled"))


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except Exception:
        return ZoneInfo("UTC")


def _setting_value(session: Session, key: str, default: str) -> str:
    setting = session.get(AppSetting, key)
    return setting.value if setting is not None else default


def _int_setting_value(session: Session, key: str, default: int) -> int:
    value = _setting_value(session, key, str(default))
    try:
        return int(value)
    except ValueError:
        return default


def _grace_seconds(grace_minutes: int) -> int:
    return max(1, grace_minutes * 60)


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _recovery_payload(recovery: DailyAnchorRecovery) -> dict:
    reset_at = _as_aware_utc(recovery.reset_at)
    due_at = _as_aware_utc(recovery.due_at)
    assert reset_at is not None and due_at is not None
    return {
        "generation": recovery.generation,
        "version": recovery.version,
        "observed_reset_at": reset_at.isoformat(),
        "retry_at": due_at.isoformat(),
        "reset_source": recovery.reset_source,
        "collision_count": recovery.collision_count,
    }
