from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ai_quota_monitor.models import Account

CODEX_API_KEY_ENV_VARS = ("CODEX_API_KEY", "OPENAI_API_KEY")


@dataclass(frozen=True)
class CodexAccountInfo:
    external_id: str | None
    display: str | None
    plan_type: str | None
    is_authenticated: bool


class DeviceLoginAttempt(Protocol):
    login_id: str
    verification_url: str
    user_code: str

    def wait(self) -> bool:
        ...

    def cancel(self) -> None:
        ...


class CodexAuthBackend(Protocol):
    def start_device_login(self, account: Account) -> DeviceLoginAttempt:
        ...

    def read_account(self, account: Account) -> CodexAccountInfo:
        ...

    def logout(self, account: Account) -> None:
        ...

    def close_login_attempt(self, attempt: DeviceLoginAttempt) -> None:
        ...


@dataclass(frozen=True)
class LoginSnapshot:
    account_id: int
    login_id: str
    verification_url: str
    user_code: str
    started_at: datetime


@dataclass
class _ActiveLogin:
    account_id: int
    attempt: DeviceLoginAttempt
    started_at: datetime


class CodexAuthManager:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        backend_factory: Callable[[], CodexAuthBackend],
    ) -> None:
        self._session_factory = session_factory
        self._backend_factory = backend_factory
        self._active_logins: dict[int, _ActiveLogin] = {}
        self._lock = threading.Lock()

    def login_snapshots(self) -> dict[int, LoginSnapshot]:
        with self._lock:
            return {
                account_id: LoginSnapshot(
                    account_id=login.account_id,
                    login_id=login.attempt.login_id,
                    verification_url=login.attempt.verification_url,
                    user_code=login.attempt.user_code,
                    started_at=login.started_at,
                )
                for account_id, login in self._active_logins.items()
            }

    def start_device_login(self, account_id: int) -> LoginSnapshot:
        with self._session_factory() as session:
            account = session.get(Account, account_id)
            if account is None:
                raise KeyError(account_id)

            backend = self._backend_factory()
            try:
                attempt = backend.start_device_login(account)
            except Exception:
                account.auth_status = "auth_failed"
                account.last_auth_check = datetime.now(UTC)
                session.commit()
                raise
            snapshot = LoginSnapshot(
                account_id=account.id,
                login_id=attempt.login_id,
                verification_url=attempt.verification_url,
                user_code=attempt.user_code,
                started_at=datetime.now(UTC),
            )

            with self._lock:
                previous = self._active_logins.pop(account.id, None)
                self._active_logins[account.id] = _ActiveLogin(
                    account_id=account.id,
                    attempt=attempt,
                    started_at=snapshot.started_at,
                )

            if previous is not None:
                previous.attempt.cancel()

            account.auth_status = "login_pending"
            account.last_auth_check = snapshot.started_at
            session.commit()

        thread = threading.Thread(
            target=self._wait_for_login,
            args=(account_id, attempt, backend),
            daemon=True,
        )
        thread.start()
        return snapshot

    def cancel_device_login(self, account_id: int) -> bool:
        with self._lock:
            login = self._active_logins.pop(account_id, None)

        if login is None:
            return False

        login.attempt.cancel()
        self._update_status(account_id, "not_configured")
        return True

    def refresh_status(self, account_id: int) -> CodexAccountInfo:
        with self._session_factory() as session:
            account = session.get(Account, account_id)
            if account is None:
                raise KeyError(account_id)

            try:
                info = self._backend_factory().read_account(account)
            except Exception:
                account.auth_status = "auth_failed"
                account.last_auth_check = datetime.now(UTC)
                session.commit()
                raise

            self._apply_account_info(session, account, info)
            session.commit()
            return info

    def logout(self, account_id: int) -> None:
        with self._lock:
            login = self._active_logins.pop(account_id, None)
        if login is not None:
            login.attempt.cancel()

        with self._session_factory() as session:
            account = session.get(Account, account_id)
            if account is None:
                raise KeyError(account_id)

            self._backend_factory().logout(account)
            account.auth_status = "not_configured"
            account.account_external_id = None
            account.account_display = None
            account.plan_type = None
            account.last_auth_check = datetime.now(UTC)
            session.commit()

    def _wait_for_login(
        self,
        account_id: int,
        attempt: DeviceLoginAttempt,
        backend: CodexAuthBackend,
    ) -> None:
        try:
            completed = attempt.wait()
            with self._session_factory() as session:
                account = session.get(Account, account_id)
                if account is None:
                    return

                if completed:
                    info = backend.read_account(account)
                    self._apply_account_info(session, account, info)
                else:
                    account.auth_status = "auth_failed"
                    account.last_auth_check = datetime.now(UTC)
                session.commit()
        except Exception:
            self._update_status(account_id, "auth_failed")
        finally:
            backend.close_login_attempt(attempt)
            with self._lock:
                active = self._active_logins.get(account_id)
                if active is not None and active.attempt is attempt:
                    self._active_logins.pop(account_id, None)

    def _update_status(self, account_id: int, auth_status: str) -> None:
        with self._session_factory() as session:
            account = session.get(Account, account_id)
            if account is None:
                return
            account.auth_status = auth_status
            account.last_auth_check = datetime.now(UTC)
            session.commit()

    def _apply_account_info(
        self,
        session: Session,
        account: Account,
        info: CodexAccountInfo,
    ) -> None:
        account.account_external_id = info.external_id
        account.account_display = info.display
        account.plan_type = info.plan_type
        account.last_auth_check = datetime.now(UTC)
        account.auth_status = "connected" if info.is_authenticated else "not_configured"

        if info.is_authenticated and info.external_id:
            duplicate = session.scalar(
                select(Account).where(
                    Account.id != account.id,
                    Account.account_external_id == info.external_id,
                )
            )
            if duplicate is not None:
                account.auth_status = "duplicate_account"


class OpenAiCodexAuthBackend:
    def start_device_login(self, account: Account) -> DeviceLoginAttempt:
        from openai_codex import Codex

        codex = Codex(config=self._config(account))
        handle = codex.login_chatgpt_device_code()
        return _OpenAiDeviceLoginAttempt(codex, handle)

    def read_account(self, account: Account) -> CodexAccountInfo:
        from openai_codex import Codex

        with Codex(config=self._config(account)) as codex:
            response = codex.account(refresh_token=False)
            account_payload = response.account
            if account_payload is None:
                return CodexAccountInfo(None, None, None, False)

            email = getattr(account_payload.root, "email", None)
            plan_type = getattr(account_payload.root, "plan_type", None)
            plan = getattr(plan_type, "value", plan_type)
            return CodexAccountInfo(email, email, plan, True)

    def logout(self, account: Account) -> None:
        from openai_codex import Codex

        with Codex(config=self._config(account)) as codex:
            codex.logout()

    def close_login_attempt(self, attempt: DeviceLoginAttempt) -> None:
        close = getattr(attempt, "close", None)
        if callable(close):
            close()

    def _config(self, account: Account):
        from openai_codex import CodexConfig

        codex_home = Path(account.codex_home).expanduser().resolve()
        workspace_path = Path(account.workspace_path).expanduser().resolve()
        codex_home.mkdir(parents=True, exist_ok=True)
        workspace_path.mkdir(parents=True, exist_ok=True)

        return CodexConfig(
            cwd=str(workspace_path),
            env={
                "CODEX_HOME": str(codex_home),
                **{key: "" for key in CODEX_API_KEY_ENV_VARS},
            },
            client_name="ai_quota_monitor",
            client_title="ai-quota-monitor",
        )


class _OpenAiDeviceLoginAttempt:
    def __init__(self, codex, handle) -> None:
        self._codex = codex
        self._handle = handle
        self.login_id = handle.login_id
        self.verification_url = handle.verification_url
        self.user_code = handle.user_code

    def wait(self) -> bool:
        result = self._handle.wait()
        return bool(result.success)

    def cancel(self) -> None:
        self._handle.cancel()
        self.close()

    def close(self) -> None:
        self._codex.close()
