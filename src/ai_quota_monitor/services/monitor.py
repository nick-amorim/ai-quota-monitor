from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ai_quota_monitor.config import Settings
from ai_quota_monitor.models import Account, UsageSnapshot
from ai_quota_monitor.services.reset_times import expected_reset_times
from ai_quota_monitor.services.scheduler import AP_DAYS, ScheduledAnchorJob

DAY_SHORT_LABELS = {
    "monday": "Mon",
    "tuesday": "Tue",
    "wednesday": "Wed",
    "thursday": "Thu",
    "friday": "Fri",
    "saturday": "Sat",
    "sunday": "Sun",
}


@dataclass(frozen=True)
class QuotaWindowView:
    key: str
    label: str
    short_label: str
    used_percent_value: float | None
    used_percent_label: str
    remaining_percent_value: float | None
    remaining_percent_label: str
    configured_label: str
    configured_short_label: str
    expected_label: str
    observed_label: str
    next_label: str
    reset_time_label: str
    reset_source_short_label: str
    reset_source_label: str
    status_label: str
    status_class: str
    drift_label: str | None = None


@dataclass(frozen=True)
class AccountMonitorView:
    account_id: int
    name: str
    slug: str
    display_label: str
    secondary_label: str | None
    enabled_label: str
    auth_label: str
    account_display: str | None
    daily_schedule_label: str
    weekly_schedule_label: str
    next_wake_label: str
    telemetry_error: str | None
    has_snapshot: bool
    captured_label: str
    snapshot_status_label: str
    snapshot_status_class: str
    windows: tuple[QuotaWindowView, QuotaWindowView]


@dataclass(frozen=True)
class TimelineEntry:
    account_name: str
    label: str
    time_label: str
    minute_percent: float
    entry_class: str


@dataclass(frozen=True)
class MonitorView:
    accounts: tuple[AccountMonitorView, ...]
    account_map: dict[int, AccountMonitorView]
    timeline: tuple[TimelineEntry, ...]
    generated_at_label: str
    generated_clock_label: str


def build_monitor_view(
    *,
    accounts: list[Account],
    usage_by_account: dict[int, UsageSnapshot],
    telemetry_errors_by_account: dict[int, str] | None = None,
    scheduled_jobs: list[ScheduledAnchorJob],
    settings: Settings,
    now: datetime | None = None,
) -> MonitorView:
    now = _as_aware_utc(now or datetime.now(UTC))
    display_timezone = _timezone(settings.timezone)
    stale_after = timedelta(minutes=max(15, settings.usage_poll_interval_minutes * 3))
    account_views = tuple(
        _account_view(
            account,
            usage_by_account.get(account.id),
            scheduled_jobs=[
                job for job in scheduled_jobs if job.account_id == account.id
            ],
            telemetry_error=(telemetry_errors_by_account or {}).get(account.id),
            now=now,
            timezone=display_timezone,
            stale_after=stale_after,
        )
        for account in accounts
    )
    timeline = build_current_day_timeline(
        accounts=accounts,
        usage_by_account=usage_by_account,
        scheduled_jobs=scheduled_jobs,
        settings=settings,
        now=now,
    )
    return MonitorView(
        accounts=account_views,
        account_map={account.account_id: account for account in account_views},
        timeline=timeline,
        generated_at_label=_format_datetime(now, display_timezone),
        generated_clock_label=_format_clock(now, display_timezone),
    )


def build_current_day_timeline(
    *,
    accounts: list[Account],
    usage_by_account: dict[int, UsageSnapshot],
    scheduled_jobs: list[ScheduledAnchorJob],
    settings: Settings,
    now: datetime | None = None,
) -> tuple[TimelineEntry, ...]:
    now = _as_aware_utc(now or datetime.now(UTC))
    timezone = _timezone(settings.timezone)
    local_now = now.astimezone(timezone)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    entries: list[TimelineEntry] = []
    account_labels = {account.id: _display_label(account) for account in accounts}

    for job in scheduled_jobs:
        entries.extend(
            _timeline_entry(
                account_name=account_labels.get(job.account_id, "Account not logged in"),
                label=f"{job.kind.title()} anchor",
                at=job.next_run_at,
                timezone=timezone,
                day_start=day_start,
                day_end=day_end,
                entry_class="anchor",
            )
        )

    for account in accounts:
        account_name = account_labels.get(account.id, "Account not logged in")
        expected = expected_reset_times(account, now)
        snapshot = usage_by_account.get(account.id)
        expected_five = (
            snapshot.five_hour_expected_reset_at
            if snapshot and snapshot.five_hour_expected_reset_at
            else expected.five_hour_reset_at
        )
        expected_weekly = (
            snapshot.weekly_expected_reset_at
            if snapshot and snapshot.weekly_expected_reset_at
            else expected.weekly_reset_at
        )
        observed_five = (
            snapshot.five_hour_observed_reset_at
            if snapshot and snapshot.five_hour_observed_reset_at
            else None
        )
        observed_weekly = (
            snapshot.weekly_observed_reset_at
            if snapshot and snapshot.weekly_observed_reset_at
            else None
        )

        entries.extend(
            _timeline_entry(
                account_name=account_name,
                label="Expected 5h reset",
                at=expected_five,
                timezone=timezone,
                day_start=day_start,
                day_end=day_end,
                entry_class="expected",
            )
        )
        entries.extend(
            _timeline_entry(
                account_name=account_name,
                label="Observed 5h reset",
                at=observed_five,
                timezone=timezone,
                day_start=day_start,
                day_end=day_end,
                entry_class="observed",
            )
        )
        entries.extend(
            _timeline_entry(
                account_name=account_name,
                label="Expected weekly reset",
                at=expected_weekly,
                timezone=timezone,
                day_start=day_start,
                day_end=day_end,
                entry_class="expected",
            )
        )
        entries.extend(
            _timeline_entry(
                account_name=account_name,
                label="Observed weekly reset",
                at=observed_weekly,
                timezone=timezone,
                day_start=day_start,
                day_end=day_end,
                entry_class="observed",
            )
        )

    return tuple(
        sorted(
            entries,
            key=lambda entry: (entry.minute_percent, entry.account_name, entry.label),
        )
    )


def _account_view(
    account: Account,
    snapshot: UsageSnapshot | None,
    *,
    scheduled_jobs: list[ScheduledAnchorJob],
    telemetry_error: str | None = None,
    now: datetime,
    timezone: ZoneInfo,
    stale_after: timedelta,
) -> AccountMonitorView:
    expected = expected_reset_times(account, now)
    stale = (
        snapshot is not None
        and _as_aware_utc(snapshot.captured_at) < now - stale_after
    )
    return AccountMonitorView(
        account_id=account.id,
        name=account.name,
        slug=account.slug,
        display_label=_display_label(account),
        secondary_label=account.plan_type,
        enabled_label="Enabled" if account.enabled else "Paused",
        auth_label=account.auth_status.replace("_", " ").title(),
        account_display=account.account_display,
        daily_schedule_label=_daily_schedule_label(account),
        weekly_schedule_label=_weekly_schedule_label(account),
        next_wake_label=_next_wake_label(scheduled_jobs, timezone),
        telemetry_error=telemetry_error,
        has_snapshot=snapshot is not None,
        captured_label=_captured_label(snapshot, telemetry_error, timezone),
        snapshot_status_label=_snapshot_status_label(snapshot, stale, telemetry_error),
        snapshot_status_class=_snapshot_status_class(snapshot, stale, telemetry_error),
        windows=(
            _window_view(
                "five-hour",
                "5-hour",
                account,
                snapshot,
                used_percent=snapshot.five_hour_used_percent if snapshot else None,
                expected_reset_at=(
                    snapshot.five_hour_expected_reset_at
                    if snapshot and snapshot.five_hour_expected_reset_at
                    else expected.five_hour_reset_at
                ),
                observed_reset_at=(
                    snapshot.five_hour_observed_reset_at
                    if snapshot and snapshot.five_hour_observed_reset_at
                    else None
                ),
                now=now,
                timezone=timezone,
                stale=stale,
                telemetry_error=telemetry_error,
            ),
            _window_view(
                "weekly",
                "Weekly",
                account,
                snapshot,
                used_percent=snapshot.weekly_used_percent if snapshot else None,
                expected_reset_at=(
                    snapshot.weekly_expected_reset_at
                    if snapshot and snapshot.weekly_expected_reset_at
                    else expected.weekly_reset_at
                ),
                observed_reset_at=(
                    snapshot.weekly_observed_reset_at
                    if snapshot and snapshot.weekly_observed_reset_at
                    else None
                ),
                now=now,
                timezone=timezone,
                stale=stale,
                telemetry_error=telemetry_error,
            ),
        ),
    )


def _window_view(
    key: str,
    label: str,
    account: Account,
    snapshot: UsageSnapshot | None,
    *,
    used_percent: float | None,
    expected_reset_at: datetime | None,
    observed_reset_at: datetime | None,
    now: datetime,
    timezone: ZoneInfo,
    stale: bool,
    telemetry_error: str | None,
) -> QuotaWindowView:
    reset_at = observed_reset_at or expected_reset_at
    remaining_percent = _remaining_percent(used_percent)
    return QuotaWindowView(
        key=key,
        label=label,
        short_label="5h" if key == "five-hour" else "7d",
        used_percent_value=used_percent,
        used_percent_label=_percent_label(used_percent),
        remaining_percent_value=remaining_percent,
        remaining_percent_label=_percent_label(remaining_percent),
        configured_label=_configured_label(account, key),
        configured_short_label=_configured_short_label(account, key),
        expected_label=_format_datetime(expected_reset_at, timezone),
        observed_label=_format_datetime(observed_reset_at, timezone),
        next_label=_next_label(reset_at, timezone, key),
        reset_time_label=_format_clock(reset_at, timezone),
        reset_source_short_label="obs" if observed_reset_at else "exp",
        reset_source_label="Observed" if observed_reset_at else "Expected",
        status_label=_window_status_label(
            snapshot,
            used_percent,
            reset_at,
            now=now,
            stale=stale,
            telemetry_error=telemetry_error,
        ),
        status_class=_window_status_class(
            snapshot,
            used_percent,
            stale=stale,
            telemetry_error=telemetry_error,
        ),
        drift_label=(
            _drift_label(observed_reset_at, expected_reset_at)
            if key == "weekly"
            else None
        ),
    )


def _timeline_entry(
    *,
    account_name: str,
    label: str,
    at: datetime | None,
    timezone: ZoneInfo,
    day_start: datetime,
    day_end: datetime,
    entry_class: str,
) -> list[TimelineEntry]:
    if at is None:
        return []

    local_at = _as_aware_utc(at).astimezone(timezone)
    if not day_start <= local_at < day_end:
        return []

    seconds = (local_at - day_start).total_seconds()
    percent = max(0.0, min(100.0, seconds / 864))
    return [
        TimelineEntry(
            account_name=account_name,
            label=label,
            time_label=local_at.strftime("%H:%M"),
            minute_percent=round(percent, 2),
            entry_class=entry_class,
        )
    ]


def _configured_label(account: Account, key: str) -> str:
    schedule = account.schedule
    if schedule is None:
        return "No schedule"
    if key == "weekly":
        return (
            f"{schedule.weekly_target_day.title()} "
            f"{_format_time(schedule.weekly_target_time)}"
        )
    if not schedule.daily_anchor_enabled:
        return "Daily anchor off"
    return f"{_format_time(schedule.daily_anchor_time)} + 5h"


def _daily_schedule_label(account: Account) -> str:
    schedule = account.schedule
    if schedule is None or not schedule.daily_anchor_enabled:
        return "Off"
    days = _active_days_label(account)
    return f"{days} {_format_time(schedule.daily_anchor_time)}"


def _weekly_schedule_label(account: Account) -> str:
    schedule = account.schedule
    if schedule is None:
        return "Off"
    day = DAY_SHORT_LABELS.get(schedule.weekly_target_day, schedule.weekly_target_day.title())
    return f"{day} {_format_time(schedule.weekly_target_time)}"


def _active_days_label(account: Account) -> str:
    schedule = account.schedule
    if schedule is None:
        return "-"

    enabled_days = [
        day
        for day in DAY_SHORT_LABELS
        if getattr(schedule, f"{day}_enabled")
    ]
    labels = [DAY_SHORT_LABELS[day] for day in enabled_days]
    if [AP_DAYS[day] for day in enabled_days] == ["mon", "tue", "wed", "thu", "fri"]:
        return "Mon-Fri"
    if len(enabled_days) == len(DAY_SHORT_LABELS):
        return "Every day"
    return ", ".join(labels) if labels else "No days"


def _next_wake_label(jobs: list[ScheduledAnchorJob], timezone: ZoneInfo) -> str:
    next_jobs = [job for job in jobs if job.next_run_at is not None]
    if not next_jobs:
        return "None"

    job = min(next_jobs, key=lambda item: _as_aware_utc(item.next_run_at))
    local_run = _as_aware_utc(job.next_run_at).astimezone(timezone)
    return f"{job.kind.title()} {local_run.strftime('%a %H:%M')}"


def _display_label(account: Account) -> str:
    return account.account_display or "Account not logged in"


def _configured_short_label(account: Account, key: str) -> str:
    schedule = account.schedule
    if schedule is None:
        return "-"
    if key == "weekly":
        day = DAY_SHORT_LABELS.get(
            schedule.weekly_target_day,
            schedule.weekly_target_day.title(),
        )
        return f"{day} {_format_time(schedule.weekly_target_time)}"
    if not schedule.daily_anchor_enabled:
        return "Off"
    return _format_time(schedule.daily_anchor_time)


def _window_status_label(
    snapshot: UsageSnapshot | None,
    used_percent: float | None,
    reset_at: datetime | None,
    *,
    now: datetime,
    stale: bool,
    telemetry_error: str | None,
) -> str:
    if telemetry_error and snapshot is None:
        return "Error"
    if snapshot is None:
        return "Waiting"
    if stale:
        return "Stale"
    if snapshot.parser_status != "ok":
        return snapshot.parser_status.title()
    if reset_at is not None and _as_aware_utc(reset_at) > now:
        return "Active"
    if used_percent is None:
        return "Unknown"
    if used_percent >= 90:
        return "High"
    if used_percent >= 70:
        return "Watch"
    return "Ready"


def _window_status_class(
    snapshot: UsageSnapshot | None,
    used_percent: float | None,
    *,
    stale: bool,
    telemetry_error: str | None,
) -> str:
    if telemetry_error and snapshot is None:
        return "error"
    if snapshot is None:
        return "waiting"
    if stale:
        return "stale"
    if snapshot.parser_status not in {"ok", "partial"}:
        return "error"
    if used_percent is None:
        return "unknown"
    if used_percent >= 90:
        return "high"
    if used_percent >= 70:
        return "watch"
    return "ready"


def _snapshot_status_label(
    snapshot: UsageSnapshot | None,
    stale: bool,
    telemetry_error: str | None,
) -> str:
    if telemetry_error and snapshot is None:
        return "Telemetry error"
    if snapshot is None:
        return "No telemetry"
    if stale:
        return f"Stale {snapshot.parser_status}"
    return snapshot.parser_status.title()


def _snapshot_status_class(
    snapshot: UsageSnapshot | None,
    stale: bool,
    telemetry_error: str | None,
) -> str:
    if telemetry_error and snapshot is None:
        return "error"
    if snapshot is None:
        return "waiting"
    if stale:
        return "stale"
    if snapshot.parser_status in {"ok", "partial"}:
        return snapshot.parser_status
    return "error"


def _drift_label(
    observed_reset_at: datetime | None,
    expected_reset_at: datetime | None,
) -> str:
    if observed_reset_at is None or expected_reset_at is None:
        return "Weekly drift unknown"

    drift_minutes = int(
        round(
            (
                _as_aware_utc(observed_reset_at)
                - _as_aware_utc(expected_reset_at)
            ).total_seconds()
            / 60
        )
    )
    if abs(drift_minutes) <= 5:
        return "Weekly drift on schedule"
    if drift_minutes > 0:
        return f"Weekly drift {drift_minutes} min late"
    return f"Weekly drift {abs(drift_minutes)} min early"


def _percent_label(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}%"


def _captured_label(
    snapshot: UsageSnapshot | None,
    telemetry_error: str | None,
    timezone: ZoneInfo,
) -> str:
    if snapshot is None and telemetry_error:
        return "Refresh failed"
    if snapshot is None:
        return "Waiting for telemetry"
    return _format_datetime(snapshot.captured_at, timezone)


def _remaining_percent(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(100.0, 100.0 - value))


def format_local_datetime(value: datetime | None, timezone_name: str) -> str:
    return _format_datetime(value, _timezone(timezone_name))


def format_local_time(value: datetime | None, timezone_name: str) -> str:
    return _format_clock(value, _timezone(timezone_name))


def _format_datetime(value: datetime | None, timezone: ZoneInfo) -> str:
    if value is None:
        return "Unknown"
    return _as_aware_utc(value).astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S")


def _format_time(value: time) -> str:
    return value.strftime("%H:%M")


def _format_clock(value: datetime | None, timezone: ZoneInfo) -> str:
    if value is None:
        return "--:--"
    return _as_aware_utc(value).astimezone(timezone).strftime("%H:%M")


def _next_label(value: datetime | None, timezone: ZoneInfo, key: str) -> str:
    if value is None:
        return "--:--"
    local_value = _as_aware_utc(value).astimezone(timezone)
    if key == "weekly":
        return local_value.strftime("%a %H:%M")
    return local_value.strftime("%H:%M")


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except Exception:
        return ZoneInfo("UTC")


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
