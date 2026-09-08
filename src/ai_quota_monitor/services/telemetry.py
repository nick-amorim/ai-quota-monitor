from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from ai_quota_monitor.models import Account, UsageRaw, UsageSnapshot
from ai_quota_monitor.services.accounts import list_accounts
from ai_quota_monitor.services.app_server import CodexAppServerClient
from ai_quota_monitor.services.reset_times import expected_reset_times

PARSER_VERSION = "rate-limits-v1"
FIVE_HOUR_WINDOW_MINUTES = 300
WEEKLY_WINDOW_MINUTES = 10080


@dataclass(frozen=True)
class RateLimitWindow:
    used_percent: float
    reset_at: datetime | None
    window_minutes: int | None


@dataclass(frozen=True)
class NormalizedUsage:
    five_hour: RateLimitWindow | None
    weekly: RateLimitWindow | None
    parser_status: str
    parser_message: str | None


@dataclass(frozen=True)
class TelemetryRefreshResult:
    account_id: int
    status: str
    snapshot: UsageSnapshot | None = None
    error: str | None = None


class TelemetryBackend(Protocol):
    def read_account(self, account: Account) -> dict[str, Any]:
        ...

    def read_rate_limits(self, account: Account) -> dict[str, Any]:
        ...


class CodexAppServerTelemetryBackend:
    def read_account(self, account: Account) -> dict[str, Any]:
        with CodexAppServerClient(account) as client:
            return client.read_account()

    def read_rate_limits(self, account: Account) -> dict[str, Any]:
        with CodexAppServerClient(account) as client:
            return client.read_rate_limits()


class TelemetryService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        backend_factory: Callable[[], TelemetryBackend],
    ) -> None:
        self._session_factory = session_factory
        self._backend_factory = backend_factory

    def refresh_account_usage(self, account_id: int) -> TelemetryRefreshResult:
        with self._session_factory() as session:
            account = session.get(Account, account_id)
            if account is None:
                raise KeyError(account_id)

        captured_at = datetime.now(UTC)
        try:
            payload = self._backend_factory().read_rate_limits(account)
        except Exception as exc:
            return TelemetryRefreshResult(account_id=account_id, status="failed", error=str(exc))

        return self.record_rate_limit_update(
            account_id,
            payload,
            captured_at=captured_at,
            source="codex-app-server",
        )

    def record_rate_limit_update(
        self,
        account_id: int,
        payload: dict[str, Any],
        *,
        captured_at: datetime | None = None,
        source: str = "codex-app-server-notification",
    ) -> TelemetryRefreshResult:
        captured_at = captured_at or datetime.now(UTC)
        with self._session_factory() as session:
            account = session.scalar(
                select(Account)
                .options(selectinload(Account.schedule))
                .where(Account.id == account_id)
            )
            if account is None:
                raise KeyError(account_id)

            raw = UsageRaw(
                account_id=account_id,
                captured_at=captured_at,
                payload_json=json.dumps(payload, sort_keys=True),
                parser_version=PARSER_VERSION,
            )
            session.add(raw)
            session.flush()

            normalized = normalize_rate_limits(payload)
            previous = latest_usage_snapshot(session, account_id)
            expected = expected_reset_times(account, captured_at)
            snapshot = _snapshot_from_normalized(
                account_id=account_id,
                captured_at=captured_at,
                raw_usage_id=raw.id,
                normalized=normalized,
                previous=previous,
                expected=expected,
                source=source,
            )
            session.add(snapshot)
            session.commit()
            session.refresh(snapshot)
            return TelemetryRefreshResult(
                account_id=account_id,
                status=snapshot.parser_status,
                snapshot=snapshot,
            )

    def refresh_all_accounts(self) -> list[TelemetryRefreshResult]:
        with self._session_factory() as session:
            account_ids = [account.id for account in list_accounts(session)]

        results = []
        for account_id in account_ids:
            try:
                results.append(self.refresh_account_usage(account_id))
            except KeyError:
                results.append(
                    TelemetryRefreshResult(
                        account_id=account_id,
                        status="failed",
                        error="Account disappeared during refresh",
                    )
                )
        return results

    def latest_snapshots_by_account(self) -> dict[int, UsageSnapshot]:
        with self._session_factory() as session:
            account_ids = [account.id for account in list_accounts(session)]
            return {
                account_id: snapshot
                for account_id in account_ids
                if (snapshot := latest_usage_snapshot(session, account_id)) is not None
            }


def latest_usage_snapshot(session: Session, account_id: int) -> UsageSnapshot | None:
    return session.scalar(
        select(UsageSnapshot)
        .options(selectinload(UsageSnapshot.account))
        .where(UsageSnapshot.account_id == account_id)
        .order_by(UsageSnapshot.captured_at.desc(), UsageSnapshot.id.desc())
        .limit(1)
    )


def normalize_rate_limits(payload: dict[str, Any]) -> NormalizedUsage:
    snapshot = _select_rate_limit_snapshot(payload)
    if not isinstance(snapshot, dict):
        return NormalizedUsage(None, None, "unsupported", "Missing rateLimits object")

    windows = [_parse_window(snapshot.get("primary")), _parse_window(snapshot.get("secondary"))]
    five_hour = _find_window(windows, FIVE_HOUR_WINDOW_MINUTES)
    weekly = _find_window(windows, WEEKLY_WINDOW_MINUTES)

    if five_hour is not None and weekly is not None:
        return NormalizedUsage(five_hour, weekly, "ok", None)
    if five_hour is not None or weekly is not None:
        return NormalizedUsage(
            five_hour,
            weekly,
            "partial",
            "Only one expected rate-limit window was present",
        )
    return NormalizedUsage(
        None,
        None,
        "unsupported",
        "No recognized 300-minute or 10080-minute rate-limit windows were present",
    )


def _select_rate_limit_snapshot(payload: dict[str, Any]) -> dict[str, Any] | None:
    by_limit_id = payload.get("rateLimitsByLimitId")
    if isinstance(by_limit_id, dict):
        codex_limits = by_limit_id.get("codex")
        if isinstance(codex_limits, dict):
            return codex_limits

    rate_limits = payload.get("rateLimits")
    return rate_limits if isinstance(rate_limits, dict) else None


def _parse_window(value: Any) -> RateLimitWindow | None:
    if not isinstance(value, dict):
        return None
    if "usedPercent" not in value:
        return None

    duration = _optional_int(value.get("windowDurationMins"))
    reset_at = _unix_seconds_to_datetime(value.get("resetsAt"))
    return RateLimitWindow(
        used_percent=float(value["usedPercent"]),
        reset_at=reset_at,
        window_minutes=duration,
    )


def _find_window(
    windows: list[RateLimitWindow | None],
    window_minutes: int,
) -> RateLimitWindow | None:
    for window in windows:
        if window is not None and window.window_minutes == window_minutes:
            return window
    return None


def _snapshot_from_normalized(
    *,
    account_id: int,
    captured_at: datetime,
    raw_usage_id: int,
    normalized: NormalizedUsage,
    previous: UsageSnapshot | None,
    expected,
    source: str = "codex-app-server",
) -> UsageSnapshot:
    five_hour = normalized.five_hour
    weekly = normalized.weekly
    five_hour_observed_reset_at = _pick(
        five_hour.reset_at if five_hour else None,
        previous.five_hour_observed_reset_at
        if previous and previous.five_hour_observed_reset_at
        else previous.five_hour_reset_at
        if previous
        else None,
    )
    weekly_observed_reset_at = _pick(
        weekly.reset_at if weekly else None,
        previous.weekly_observed_reset_at
        if previous and previous.weekly_observed_reset_at
        else previous.weekly_reset_at
        if previous
        else None,
    )
    return UsageSnapshot(
        account_id=account_id,
        captured_at=captured_at,
        five_hour_used_percent=_pick(
            five_hour.used_percent if five_hour else None,
            previous.five_hour_used_percent if previous else None,
        ),
        five_hour_window_minutes=_pick(
            five_hour.window_minutes if five_hour else None,
            previous.five_hour_window_minutes if previous else None,
        ),
        five_hour_reset_at=_pick(
            five_hour.reset_at if five_hour else None,
            previous.five_hour_reset_at if previous else None,
        ),
        five_hour_expected_reset_at=(
            expected.five_hour_reset_at if expected is not None else None
        ),
        five_hour_observed_reset_at=five_hour_observed_reset_at,
        weekly_used_percent=_pick(
            weekly.used_percent if weekly else None,
            previous.weekly_used_percent if previous else None,
        ),
        weekly_window_minutes=_pick(
            weekly.window_minutes if weekly else None,
            previous.weekly_window_minutes if previous else None,
        ),
        weekly_reset_at=_pick(
            weekly.reset_at if weekly else None,
            previous.weekly_reset_at if previous else None,
        ),
        weekly_expected_reset_at=(
            expected.weekly_reset_at if expected is not None else None
        ),
        weekly_observed_reset_at=weekly_observed_reset_at,
        parser_status=normalized.parser_status,
        parser_message=normalized.parser_message,
        source=source,
        raw_usage_id=raw_usage_id,
    )


def _pick(new_value: Any, previous_value: Any) -> Any:
    return new_value if new_value is not None else previous_value


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _unix_seconds_to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), UTC)
