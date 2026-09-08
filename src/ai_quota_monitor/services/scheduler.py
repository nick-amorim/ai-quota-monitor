from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, sessionmaker

from ai_quota_monitor.config import Settings
from ai_quota_monitor.models import Account, AppSetting
from ai_quota_monitor.services.accounts import WEEKDAYS, list_accounts
from ai_quota_monitor.services.anchors import AnchorAlreadyRunningError, AnchorService
from ai_quota_monitor.services.events import record_event

SCHEDULER_JOB_PREFIX = "anchor:"
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


class QuotaScheduler:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        anchor_service: AnchorService,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._anchor_service = anchor_service
        self._settings = settings
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
        runtime_config = self._runtime_config()

        added = 0
        with self._session_factory() as session:
            accounts = list_accounts(session)

        for account in accounts:
            if not account.enabled or account.schedule is None:
                continue

            if account.schedule.daily_anchor_enabled:
                active_days = _active_weekdays(account)
                if active_days:
                    scheduler.add_job(
                        self.run_anchor_job,
                        trigger=CronTrigger(
                            day_of_week=",".join(active_days),
                            hour=account.schedule.daily_anchor_time.hour,
                            minute=account.schedule.daily_anchor_time.minute,
                            timezone=_timezone(account.schedule.timezone),
                        ),
                        args=(account.id, "daily"),
                        id=_job_id(account.id, "daily"),
                        misfire_grace_time=runtime_config.misfire_grace_time,
                        name=f"{account.name} daily anchor",
                        replace_existing=True,
                    )
                    added += 1

            scheduler.add_job(
                self.run_anchor_job,
                trigger=CronTrigger(
                    day_of_week=AP_DAYS[account.schedule.weekly_target_day],
                    hour=account.schedule.weekly_target_time.hour,
                    minute=account.schedule.weekly_target_time.minute,
                    timezone=_timezone(account.schedule.timezone),
                ),
                args=(account.id, "weekly"),
                id=_job_id(account.id, "weekly"),
                misfire_grace_time=runtime_config.misfire_grace_time,
                name=f"{account.name} weekly anchor",
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

            _, account_id_raw, kind = job.id.split(":", maxsplit=2)
            account_id = int(account_id_raw)
            jobs.append(
                ScheduledAnchorJob(
                    id=job.id,
                    account_id=account_id,
                    account_name=account_names.get(account_id, f"Account {account_id}"),
                    kind=kind,
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
            self._anchor_service.run_manual_anchor(account_id)
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
            self._record_event(
                level="info",
                category="scheduler.anchor",
                message=f"Scheduled {kind} anchor completed",
                account_id=account_id,
            )

    def _require_scheduler(self) -> BackgroundScheduler:
        if self._scheduler is None:
            raise RuntimeError("Scheduler has not been started")
        return self._scheduler

    def _remove_anchor_jobs(self) -> None:
        scheduler = self._require_scheduler()
        for job in scheduler.get_jobs():
            if job.id.startswith(SCHEDULER_JOB_PREFIX):
                scheduler.remove_job(job.id)

    def _record_apscheduler_event(self, event: JobExecutionEvent) -> None:
        if not event.job_id.startswith(SCHEDULER_JOB_PREFIX):
            return

        _, account_id_raw, kind = event.job_id.split(":", maxsplit=2)
        account_id = int(account_id_raw)
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


def _job_id(account_id: int, kind: str) -> str:
    return f"{SCHEDULER_JOB_PREFIX}{account_id}:{kind}"


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
