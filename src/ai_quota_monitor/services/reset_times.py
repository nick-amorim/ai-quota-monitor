from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ai_quota_monitor.models import Account, UsageSnapshot
from ai_quota_monitor.services.accounts import WEEKDAYS

WEEKDAY_INDEXES = {weekday: index for index, weekday in enumerate(WEEKDAYS)}
FIVE_HOUR_WINDOW_MINUTES = 300


@dataclass(frozen=True)
class ExpectedResetTimes:
    five_hour_reset_at: datetime | None


def expected_reset_times(
    account: Account,
    captured_at: datetime,
) -> ExpectedResetTimes:
    schedule = account.schedule
    if schedule is None:
        return ExpectedResetTimes(None)

    timezone = _timezone(schedule.timezone)
    local_now = _as_aware_utc(captured_at).astimezone(timezone)

    five_hour_reset_at = None
    if schedule.daily_anchor_enabled and not schedule.anchor_paused:
        active_days = [
            WEEKDAY_INDEXES[weekday]
            for weekday in WEEKDAYS
            if getattr(schedule, f"{weekday}_enabled")
        ]
        next_daily_anchor = _next_local_occurrence(
            local_now,
            active_days,
            schedule.daily_anchor_time,
        )
        if next_daily_anchor is not None:
            five_hour_reset_at = (
                next_daily_anchor + timedelta(minutes=FIVE_HOUR_WINDOW_MINUTES)
            ).astimezone(UTC)

    return ExpectedResetTimes(five_hour_reset_at)


def observed_reset_at(
    snapshot: UsageSnapshot | None,
    *,
    kind: str,
) -> datetime | None:
    if snapshot is None:
        return None

    if kind == "weekly":
        return _as_aware_utc(
            snapshot.weekly_observed_reset_at or snapshot.weekly_reset_at
        )
    return _as_aware_utc(
        snapshot.five_hour_observed_reset_at or snapshot.five_hour_reset_at
    )


def five_hour_window_is_active(
    snapshot: UsageSnapshot | None,
    *,
    now: datetime,
) -> bool:
    reset_at = observed_reset_at(snapshot, kind="daily")
    if reset_at is None:
        return False
    return reset_at > _as_aware_utc(now)


def _next_local_occurrence(
    local_now: datetime,
    weekday_indexes: list[int],
    at_time: time,
) -> datetime | None:
    if not weekday_indexes:
        return None

    candidates = []
    for days_ahead in range(8):
        candidate_date = local_now.date() + timedelta(days=days_ahead)
        if candidate_date.weekday() not in weekday_indexes:
            continue

        candidate = datetime.combine(
            candidate_date,
            at_time,
            tzinfo=local_now.tzinfo,
        )
        if candidate >= local_now:
            candidates.append(candidate)

    return min(candidates) if candidates else None


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except Exception:
        return ZoneInfo("UTC")


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
