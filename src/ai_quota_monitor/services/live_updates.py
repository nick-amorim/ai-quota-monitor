from __future__ import annotations

import threading
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ai_quota_monitor.config import Settings
from ai_quota_monitor.models import Account
from ai_quota_monitor.services.app_server import (
    CodexAppServerClient,
    CodexAppServerNotification,
)
from ai_quota_monitor.services.events import record_event
from ai_quota_monitor.services.telemetry import TelemetryService

AppServerClientFactory = Callable[..., CodexAppServerClient]


class RateLimitUpdateListener:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        telemetry_service: TelemetryService,
        settings: Settings,
        *,
        client_factory: AppServerClientFactory = CodexAppServerClient,
    ) -> None:
        self._session_factory = session_factory
        self._telemetry_service = telemetry_service
        self._settings = settings
        self._client_factory = client_factory
        self._clients: dict[int, CodexAppServerClient] = {}
        self._lock = threading.Lock()

    @property
    def active_account_ids(self) -> list[int]:
        with self._lock:
            return sorted(self._clients)

    def start(self) -> None:
        self.reload()

    def reload(self) -> None:
        if not self._settings.enable_app_server_notifications:
            self.shutdown()
            return

        with self._session_factory() as session:
            accounts = list(
                session.scalars(
                    select(Account).where(
                        Account.enabled.is_(True),
                        Account.auth_status == "connected",
                    )
                )
            )

        wanted_ids = {account.id for account in accounts}
        with self._lock:
            for account_id in set(self._clients).difference(wanted_ids):
                self._close_account(account_id)

        for account in accounts:
            self._ensure_account_listener(account)

    def shutdown(self) -> None:
        with self._lock:
            account_ids = list(self._clients)
            for account_id in account_ids:
                self._close_account(account_id)

    def _ensure_account_listener(self, account: Account) -> None:
        with self._lock:
            if account.id in self._clients:
                return

        client: CodexAppServerClient | None = None
        try:
            client = self._client_factory(
                account,
                notification_handler=lambda notification, account_id=account.id: (
                    self._handle_notification(account_id, notification)
                ),
            )
            client.start()
            client.initialize()
        except Exception as exc:
            self._record_start_failure(account.id, exc, client)
            return

        with self._lock:
            self._clients[account.id] = client
        self._record_event(
            "info",
            "live-updates",
            "Rate-limit notification listener started",
            account.id,
            {},
        )

    def _record_start_failure(
        self,
        account_id: int,
        exc: Exception,
        client: CodexAppServerClient | None,
    ) -> None:
        if client is not None:
            client.close()
        self._record_event(
            "warning",
            "live-updates",
            "Rate-limit notification listener failed to start",
            account_id,
            {"error": str(exc)},
        )

    def _close_account(self, account_id: int) -> None:
        client = self._clients.pop(account_id, None)
        if client is None:
            return
        client.close()
        self._record_event(
            "info",
            "live-updates",
            "Rate-limit notification listener stopped",
            account_id,
            {},
        )

    def _handle_notification(
        self,
        account_id: int,
        notification: CodexAppServerNotification,
    ) -> None:
        if notification.method != "account/rateLimits/updated":
            return
        if notification.params is None:
            return

        try:
            result = self._telemetry_service.record_rate_limit_update(
                account_id,
                notification.params,
                source="codex-app-server-notification",
            )
        except Exception as exc:
            self._record_event(
                "warning",
                "live-updates",
                "Rate-limit notification ingest failed",
                account_id,
                {"error": str(exc)},
            )
            return

        self._record_event(
            "info",
            "live-updates",
            "Rate-limit notification ingested",
            account_id,
            {"snapshot_status": result.status},
        )

    def _record_event(
        self,
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
