from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ai_quota_monitor.models import UsageRaw, UsageSnapshot
from ai_quota_monitor.services.telemetry import (
    FIVE_HOUR_WINDOW_MINUTES,
    WEEKLY_WINDOW_MINUTES,
    normalize_rate_limits,
)

_USAGE_LIMIT_RE = re.compile(
    r"\b(?:usage\s*limit\s+exceeded|you(?:'|’)ve\s+hit\s+your\s+usage\s+limit)\b",
    re.IGNORECASE,
)
_RETRY_TIME_RE = re.compile(
    r"\btry\s+again\s+at\s+"
    r"(?P<hour>1[0-2]|0?[1-9]):(?P<minute>[0-5]\d)\s*(?P<ampm>a\.?m\.?|p\.?m\.?)\s*[.!]?\s*$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class RecoveryReset:
    reset_at: datetime
    source: str
    detail: dict[str, Any]


def is_usage_limit_error(exc: BaseException) -> bool:
    """Deliberately narrow: ordinary runtime errors must not become retries."""
    if _USAGE_LIMIT_RE.search(str(exc)):
        return True
    data = getattr(exc, "data", None)
    return _has_usage_limit_marker(data)


def structured_reset_from_error(exc: BaseException, *, now: datetime) -> RecoveryReset | None:
    """Use explicit runtime reset metadata when a future SDK/runtime exposes it."""
    if not is_usage_limit_error(exc):
        return None
    value = _find_reset_value(getattr(exc, "data", None))
    if value is None:
        value = _find_reset_value(getattr(exc, "reset_at", None))
    reset_at = _coerce_provider_instant(value)
    if reset_at is None or reset_at <= _utc(now):
        return None
    return RecoveryReset(reset_at, "provider-error-metadata", {"metadata_field": "reset"})


def fresh_blocking_reset(snapshot: UsageSnapshot | None, raw: UsageRaw | None, *, now: datetime) -> RecoveryReset | None:
    """Choose only windows present in this raw payload, never inherited snapshot fields."""
    if snapshot is None or raw is None or snapshot.raw_usage_id != raw.id:
        return None
    try:
        payload = json.loads(raw.payload_json)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        normalized = normalize_rate_limits(payload)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    weekly = normalized.weekly
    # An exhausted weekly window without its reset means availability is unknown.
    # Do not schedule at an earlier 5-hour reset in that case.
    if weekly is not None and weekly.used_percent >= 100 and weekly.reset_at is None:
        return None
    windows = (normalized.five_hour, weekly)
    blocked = [
        window
        for window in windows
        if window is not None
        and window.window_minutes in {FIVE_HOUR_WINDOW_MINUTES, WEEKLY_WINDOW_MINUTES}
        and window.used_percent >= 100
        and window.reset_at is not None
        and _utc(window.reset_at) > _utc(now)
    ]
    if not blocked:
        return None
    reset_at = max(_utc(window.reset_at) for window in blocked if window.reset_at)
    return RecoveryReset(
        reset_at=reset_at,
        source="fresh-telemetry",
        detail={
            "raw_usage_id": raw.id,
            "snapshot_id": snapshot.id,
            "blocking_windows": [window.window_minutes for window in blocked],
        },
    )


def fresh_active_window_reset(
    snapshot: UsageSnapshot | None, raw: UsageRaw | None, *, now: datetime
) -> RecoveryReset | None:
    """Confirm that a preflight skip's 5h reset appeared in this payload."""
    if snapshot is None or raw is None or snapshot.raw_usage_id != raw.id:
        return None
    try:
        payload = json.loads(raw.payload_json)
        normalized = normalize_rate_limits(payload) if isinstance(payload, dict) else None
    except (TypeError, ValueError, OverflowError, OSError, json.JSONDecodeError):
        return None
    window = normalized.five_hour if normalized is not None else None
    if window is None or window.reset_at is None or _utc(window.reset_at) <= _utc(now):
        return None
    if (
        normalized.weekly is not None
        and normalized.weekly.used_percent >= 100
        and normalized.weekly.reset_at is None
    ):
        return None
    weekly_block = fresh_blocking_reset(snapshot, raw, now=now)
    reset_at = max(
        _utc(window.reset_at),
        weekly_block.reset_at if weekly_block is not None else _utc(window.reset_at),
    )
    return RecoveryReset(
        reset_at=reset_at,
        source="fresh-active-window-telemetry",
        detail={"raw_usage_id": raw.id, "snapshot_id": snapshot.id,
                "weekly_blocking_reset": weekly_block.reset_at.isoformat() if weekly_block else None},
    )


def parse_usage_limit_reset(
    message: str,
    *,
    failed_at: datetime,
    timezone_name: str,
) -> RecoveryReset | None:
    """Parse only the reported bare 12-hour wording, with an explicit local assumption."""
    if not _USAGE_LIMIT_RE.search(message):
        return None
    match = _RETRY_TIME_RE.search(message)
    if match is None:
        return None
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if match.group("ampm").lower().replace(".", "").startswith("p") and hour != 12:
        hour += 12
    if match.group("ampm").lower().replace(".", "").startswith("a") and hour == 12:
        hour = 0
    local_failed = _utc(failed_at).astimezone(timezone)
    candidate_date = local_failed.date()
    candidate = datetime.combine(candidate_date, time(hour, minute), tzinfo=timezone)
    # A bare time after the failure is understood as the following local date.
    if candidate + timedelta(minutes=1) <= local_failed:
        candidate = datetime.combine(candidate_date + timedelta(days=1), time(hour, minute), tzinfo=timezone)
    if not _is_unambiguous_existing_local(candidate, timezone):
        return None
    reset_at = candidate.astimezone(UTC)
    if reset_at + timedelta(minutes=1) <= _utc(failed_at):
        return None
    return RecoveryReset(
        reset_at=reset_at,
        source="usage-limit-text-fallback",
        detail={"timezone_assumption": timezone.key, "message_format": "try again at H:MM AM/PM"},
    )


def _is_unambiguous_existing_local(value: datetime, timezone: ZoneInfo) -> bool:
    instants = {
        value.replace(tzinfo=timezone, fold=fold).astimezone(UTC)
        for fold in (0, 1)
        if value.replace(tzinfo=timezone, fold=fold).astimezone(UTC).astimezone(timezone).replace(tzinfo=None)
        == value.replace(tzinfo=None)
    }
    return len(instants) == 1


def _has_usage_limit_marker(value: Any) -> bool:
    if isinstance(value, dict):
        for key in ("codex_error_info", "codexErrorInfo", "errorInfo", "type", "code"):
            marker = value.get(key)
            if isinstance(marker, str) and marker.replace("_", "").lower() == "usagelimitexceeded":
                return True
            if _has_usage_limit_marker(marker):
                return True
    if isinstance(value, list):
        return any(_has_usage_limit_marker(item) for item in value)
    return False


def _find_reset_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("resetsAt", "resetAt", "reset_at"):
            if key in value:
                return value[key]
        for key in ("codex_error_info", "codexErrorInfo", "errorInfo", "data"):
            found = _find_reset_value(value.get(key))
            if found is not None:
                return found
    return value if isinstance(value, (int, float, datetime)) else None


def _coerce_provider_instant(value: Any) -> datetime | None:
    try:
        if isinstance(value, datetime):
            return _utc(value) if value.tzinfo is not None else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
