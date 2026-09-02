from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from ai_quota_monitor.config import Settings
from ai_quota_monitor.models import Account, AnchorRun, AppSetting
from ai_quota_monitor.services.codex_auth import codex_config_for_account


class AnchorAlreadyRunningError(RuntimeError):
    pass


class AnchorAccountNotReadyError(RuntimeError):
    pass


@dataclass(frozen=True)
class AnchorTurnResult:
    status: str
    thread_id: str | None
    turn_id: str | None
    final_response: str | None
    token_usage: dict[str, Any] | None
    duration_ms: int | None


class CodexAnchorBackend(Protocol):
    def run_anchor(self, account: Account, prompt: str) -> AnchorTurnResult:
        ...


class AnchorService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        backend_factory: Callable[[], CodexAnchorBackend],
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._backend_factory = backend_factory
        self._locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def run_manual_anchor(self, account_id: int) -> AnchorRun:
        account, prompt = self._load_anchor_inputs(account_id)
        lock = self._account_lock(account_id)
        if not lock.acquire(blocking=False):
            raise AnchorAlreadyRunningError(
                f"Anchor already running for account {account_id}"
            )

        started = datetime.now(UTC)
        run_id = self._create_run(account_id, prompt, started)
        timer_started = monotonic()

        try:
            if account.auth_status != "connected":
                raise AnchorAccountNotReadyError(
                    f"{account.name} must be connected before running an anchor"
                )

            result = self._backend_factory().run_anchor(account, prompt)
            return self._complete_run(run_id, result)
        except Exception as exc:
            duration_ms = int((monotonic() - timer_started) * 1000)
            self._fail_run(run_id, exc, duration_ms)
            raise
        finally:
            lock.release()

    def recent_runs(self, limit: int = 10) -> list[AnchorRun]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(AnchorRun)
                    .options(selectinload(AnchorRun.account))
                    .order_by(AnchorRun.started_at.desc(), AnchorRun.id.desc())
                    .limit(limit)
                )
            )

    def _load_anchor_inputs(self, account_id: int) -> tuple[Account, str]:
        with self._session_factory() as session:
            account = session.get(Account, account_id)
            if account is None:
                raise KeyError(account_id)

            setting = session.get(AppSetting, "anchor_prompt")
            prompt = setting.value if setting is not None else self._settings.anchor_prompt
            return account, prompt

    def _account_lock(self, account_id: int) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(account_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[account_id] = lock
            return lock

    def _create_run(self, account_id: int, prompt: str, started_at: datetime) -> int:
        with self._session_factory() as session:
            run = AnchorRun(
                account_id=account_id,
                status="running",
                prompt=prompt,
                started_at=started_at,
            )
            session.add(run)
            session.commit()
            return run.id

    def _complete_run(self, run_id: int, result: AnchorTurnResult) -> AnchorRun:
        with self._session_factory() as session:
            run = session.get(AnchorRun, run_id)
            if run is None:
                raise KeyError(run_id)

            run.status = result.status
            run.thread_id = result.thread_id
            run.turn_id = result.turn_id
            run.final_response = result.final_response
            run.token_usage_json = _json_or_none(result.token_usage)
            run.duration_ms = result.duration_ms
            run.completed_at = datetime.now(UTC)
            session.commit()
            session.refresh(run)
            return run

    def _fail_run(self, run_id: int, exc: Exception, duration_ms: int) -> None:
        with self._session_factory() as session:
            run = session.get(AnchorRun, run_id)
            if run is None:
                return

            run.status = "failed"
            run.error_message = str(exc)
            run.duration_ms = duration_ms
            run.completed_at = datetime.now(UTC)
            session.commit()


class CodexSdkAnchorBackend:
    def run_anchor(self, account: Account, prompt: str) -> AnchorTurnResult:
        from openai_codex import ApprovalMode, Codex, Sandbox

        with Codex(config=codex_config_for_account(account)) as codex:
            thread = codex.thread_start(
                approval_mode=ApprovalMode.deny_all,
                cwd=account.workspace_path,
                ephemeral=True,
                sandbox=Sandbox.read_only,
            )
            result = thread.run(
                prompt,
                approval_mode=ApprovalMode.deny_all,
                cwd=account.workspace_path,
                sandbox=Sandbox.read_only,
            )

        return AnchorTurnResult(
            status=_string_value(result.status),
            thread_id=thread.id,
            turn_id=result.id,
            final_response=result.final_response,
            token_usage=_model_to_dict(result.usage),
            duration_ms=result.duration_ms,
        )


def get_app_setting(
    session: Session,
    key: str,
    default: str | None = None,
) -> str | None:
    setting = session.get(AppSetting, key)
    return setting.value if setting is not None else default


def update_app_setting(session: Session, key: str, value: str) -> AppSetting:
    setting = session.get(AppSetting, key)
    if setting is None:
        setting = AppSetting(key=key, value=value)
        session.add(setting)
    else:
        setting.value = value

    session.commit()
    session.refresh(setting)
    return setting


def _json_or_none(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def _model_to_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {"value": str(value)}


def _string_value(value: Any) -> str:
    return str(getattr(value, "value", value))
