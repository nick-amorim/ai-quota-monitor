from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from ai_quota_monitor.config import Settings
from ai_quota_monitor.models import Account, AnchorRun, UsageSnapshot
from ai_quota_monitor.services.anchors import AnchorService
from ai_quota_monitor.services.events import record_event
from ai_quota_monitor.services.reset_times import (
    five_hour_window_is_active,
    observed_reset_at,
)
from ai_quota_monitor.services.telemetry import TelemetryRefreshResult, TelemetryService


@dataclass(frozen=True)
class SmartAnchorResult:
    account_id: int
    kind: str
    decision: str
    reason: str
    verification_status: str
    pre_snapshot_id: int | None = None
    post_snapshot_id: int | None = None
    anchor_run_id: int | None = None
    observed_reset_before: datetime | None = None
    observed_reset_after: datetime | None = None
    error: str | None = None


class SmartAnchorService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        anchor_service: AnchorService,
        telemetry_service: TelemetryService,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._anchor_service = anchor_service
        self._telemetry_service = telemetry_service

    def run_scheduled_anchor(self, account_id: int, kind: str) -> SmartAnchorResult:
        account = self._load_account(account_id)
        if account.schedule is not None and account.schedule.anchor_paused:
            self._record_event(
                level="info",
                category="smart-anchor",
                message=f"Scheduled {kind} anchor skipped because anchors are paused",
                account_id=account_id,
                payload={},
            )
            return SmartAnchorResult(
                account_id=account_id,
                kind=kind,
                decision="skipped_paused",
                reason="scheduled anchors are paused",
                verification_status="skipped",
            )

        pre_refresh = self._refresh_usage(account_id, "pre-anchor")
        pre_snapshot = pre_refresh.snapshot if pre_refresh is not None else None

        now = datetime.now(UTC)
        if (
            account.schedule is not None
            and account.schedule.skip_if_window_active
            and five_hour_window_is_active(pre_snapshot, now=now)
        ):
            reset_at = observed_reset_at(pre_snapshot, kind="daily")
            self._record_event(
                level="info",
                category="smart-anchor",
                message=f"Scheduled {kind} anchor skipped because 5-hour window is active",
                account_id=account_id,
                payload={
                    "pre_snapshot_id": _snapshot_id(pre_snapshot),
                    "observed_five_hour_reset_at": _isoformat(reset_at),
                },
            )
            return SmartAnchorResult(
                account_id=account_id,
                kind=kind,
                decision="skipped_active_window",
                reason="5-hour window is already active",
                verification_status="skipped",
                pre_snapshot_id=_snapshot_id(pre_snapshot),
                observed_reset_before=reset_at,
            )

        anchor_run = self._anchor_service.run_manual_anchor(account_id)
        post_refresh = self._refresh_usage(account_id, "post-anchor")
        post_snapshot = post_refresh.snapshot if post_refresh is not None else None
        verification_status = _verify_reset(
            kind=kind,
            before=pre_snapshot,
            after=post_snapshot,
            now=now,
        )
        before_reset = observed_reset_at(pre_snapshot, kind=kind)
        after_reset = observed_reset_at(post_snapshot, kind=kind)
        decision = "sent"
        reason = "anchor run completed"
        if pre_refresh is None or pre_refresh.snapshot is None:
            decision = "sent_without_pre_telemetry"
            reason = "pre-anchor telemetry was unavailable"
        if post_refresh is None or post_refresh.snapshot is None:
            verification_status = "unknown"

        self._record_event(
            level="info" if verification_status == "verified" else "warning",
            category="smart-anchor",
            message=f"Scheduled {kind} anchor reset verification {verification_status}",
            account_id=account_id,
            payload={
                "anchor_run_id": anchor_run.id,
                "decision": decision,
                "pre_snapshot_id": _snapshot_id(pre_snapshot),
                "post_snapshot_id": _snapshot_id(post_snapshot),
                "observed_reset_before": _isoformat(before_reset),
                "observed_reset_after": _isoformat(after_reset),
            },
        )
        return SmartAnchorResult(
            account_id=account_id,
            kind=kind,
            decision=decision,
            reason=reason,
            verification_status=verification_status,
            pre_snapshot_id=_snapshot_id(pre_snapshot),
            post_snapshot_id=_snapshot_id(post_snapshot),
            anchor_run_id=anchor_run.id,
            observed_reset_before=before_reset,
            observed_reset_after=after_reset,
            error=post_refresh.error if post_refresh is not None else None,
        )

    def _load_account(self, account_id: int) -> Account:
        with self._session_factory() as session:
            account = session.scalar(
                select(Account)
                .options(selectinload(Account.schedule))
                .where(
                    Account.id == account_id,
                    Account.archived_at.is_(None),
                )
            )
            if account is None:
                raise KeyError(account_id)
            return account

    def _refresh_usage(
        self,
        account_id: int,
        phase: str,
    ) -> TelemetryRefreshResult | None:
        try:
            result = self._telemetry_service.refresh_account_usage(account_id)
        except Exception as exc:
            self._record_event(
                level="warning",
                category="smart-anchor.telemetry",
                message=f"{phase} telemetry refresh failed",
                account_id=account_id,
                payload={"error": str(exc)},
            )
            return None

        if result.snapshot is None:
            self._record_event(
                level="warning",
                category="smart-anchor.telemetry",
                message=f"{phase} telemetry refresh returned no snapshot",
                account_id=account_id,
                payload={"status": result.status, "error": result.error},
            )
        return result

    def _record_event(
        self,
        *,
        level: str,
        category: str,
        message: str,
        account_id: int,
        payload: dict,
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


def _verify_reset(
    *,
    kind: str,
    before: UsageSnapshot | None,
    after: UsageSnapshot | None,
    now: datetime,
) -> str:
    after_reset = observed_reset_at(after, kind=kind)
    if after_reset is None:
        return "unknown"

    before_reset = observed_reset_at(before, kind=kind)
    if before_reset is None:
        return "verified" if after_reset > now else "unverified"
    if after_reset > before_reset:
        return "verified"
    if after_reset == before_reset:
        return "unchanged"
    return "unverified"


def _snapshot_id(snapshot: UsageSnapshot | None) -> int | None:
    return snapshot.id if snapshot is not None else None


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
