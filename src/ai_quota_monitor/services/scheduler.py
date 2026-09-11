from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session, sessionmaker

from ai_quota_monitor.config import Settings
from ai_quota_monitor.models import Account, AppSetting
from ai_quota_monitor.services.accounts import WEEKDAYS, list_accounts
from ai_quota_monitor.services.anchors import AnchorAlreadyRunningError
from ai_quota_monitor.services.events import record_event
from ai_quota_monitor.services.reset_times import FIVE_HOUR_WINDOW_MINUTES
from ai_quota_monitor.services.smart_anchors import SmartAnchorResult

SCHEDULER_JOB_PREFIX = "anchor:"
TELEMETRY_JOB_ID = "telemetry:refresh-all"
AP_DAYS = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}


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
        self._remove_anchor_jobs()
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
                    timezone=str(job.trigger.timezone),
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
                level="warning",
                category="scheduler.anchor",
                message=f"Scheduled {kind} anchor skipped because another run is active",
                account_id=account_id,
                payload={"error": str(exc)},
            )
        except Exception as exc:
            self._record_event(
                level="error",
                category="scheduler.anchor",
                message=f"Scheduled {kind} anchor failed",
                account_id=account_id,
                payload={"error": str(exc)},
            )
            raise
        else:
            verb = "completed"
            level = "info"
            if result.decision.startswith("skipped"):
                verb = "skipped"
            elif result.verification_status != "verified":
                level = "warning"
            self._record_event(
                level=level,
                category="scheduler.anchor",
                message=f"Scheduled {kind} anchor {verb}",
                account_id=account_id,
                payload={
                    "decision": result.decision,
                    "verification_status": result.verification_status,
                    "anchor_run_id": result.anchor_run_id,
                },
            )

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

    def _require_scheduler(self) -> BackgroundScheduler:
        if self._scheduler is None:
            raise RuntimeError("Scheduler has not been started")
        return self._scheduler

    def _remove_anchor_jobs(self) -> None:
        scheduler = self._require_scheduler()
        for job in scheduler.get_jobs():
            if job.id.startswith(SCHEDULER_JOB_PREFIX):
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


def _parse_job_id(job_id: str) -> tuple[int, str, int | None]:
    parts = job_id.split(":")
    if len(parts) < 3 or parts[0] != "anchor":
        raise ValueError(f"Invalid scheduler job id: {job_id}")

    sequence = int(parts[3]) if len(parts) > 3 else None
    return int(parts[1]), parts[2], sequence


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
