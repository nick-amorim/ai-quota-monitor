from __future__ import annotations

import time
from dataclasses import dataclass

from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.models import Account
from ai_quota_monitor.services.accounts import list_accounts, seed_defaults
from ai_quota_monitor.services.codex_auth import (
    CodexAccountInfo,
    CodexAuthManager,
    DeviceLoginAttempt,
    OpenAiCodexAuthBackend,
)


@dataclass
class FakeAttempt:
    login_id: str = "login-1"
    verification_url: str = "https://example.test/device"
    user_code: str = "ABCD-EFGH"
    completed: bool = True
    cancelled: bool = False
    closed: bool = False

    def wait(self) -> bool:
        return self.completed

    def cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.closed = True


class FakeBackend:
    info = CodexAccountInfo(
        external_id="user@example.test",
        display="user@example.test",
        plan_type="plus",
        is_authenticated=True,
    )
    attempt = FakeAttempt()
    logged_out = False

    def start_device_login(self, account: Account) -> DeviceLoginAttempt:
        return self.attempt

    def read_account(self, account: Account) -> CodexAccountInfo:
        return self.info

    def logout(self, account: Account) -> None:
        self.logged_out = True

    def close_login_attempt(self, attempt: DeviceLoginAttempt) -> None:
        close = getattr(attempt, "close", None)
        if close:
            close()


class FailingBackend(FakeBackend):
    def read_account(self, account: Account) -> CodexAccountInfo:
        raise RuntimeError("codex unavailable")


def make_manager(tmp_path, backend):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ai-quota-monitor.sqlite3'}",
        data_dir=tmp_path,
    )
    run_migrations(settings)
    engine = create_database_engine(settings)
    initialize_database(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        seed_defaults(session, settings)

    return engine, session_factory, CodexAuthManager(session_factory, lambda: backend)


def wait_for_status(session_factory, account_id: int, status: str) -> Account:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with session_factory() as session:
            account = session.get(Account, account_id)
            if account is not None and account.auth_status == status:
                return account
        time.sleep(0.01)
    raise AssertionError(f"account {account_id} did not reach {status}")


def test_start_device_login_returns_code_and_updates_account(tmp_path):
    backend = FakeBackend()
    engine, session_factory, manager = make_manager(tmp_path, backend)

    snapshot = manager.start_device_login(1)
    account = wait_for_status(session_factory, 1, "connected")

    assert snapshot.verification_url == "https://example.test/device"
    assert snapshot.user_code == "ABCD-EFGH"
    assert account.account_display == "user@example.test"
    assert account.plan_type == "plus"
    assert backend.attempt.closed is True
    engine.dispose()


def test_refresh_status_detects_duplicate_account_identity(tmp_path):
    backend = FakeBackend()
    engine, session_factory, manager = make_manager(tmp_path, backend)

    with session_factory() as session:
        accounts = list_accounts(session)
        accounts[0].account_external_id = "user@example.test"
        accounts[0].account_display = "user@example.test"
        accounts[0].auth_status = "connected"
        session.commit()

    manager.refresh_status(2)

    with session_factory() as session:
        account_b = session.get(Account, 2)

    assert account_b is not None
    assert account_b.auth_status == "duplicate_account"
    engine.dispose()


def test_logout_clears_account_metadata(tmp_path):
    backend = FakeBackend()
    engine, session_factory, manager = make_manager(tmp_path, backend)

    manager.refresh_status(1)
    manager.logout(1)

    with session_factory() as session:
        account = session.get(Account, 1)

    assert backend.logged_out is True
    assert account is not None
    assert account.auth_status == "not_configured"
    assert account.account_external_id is None
    assert account.account_display is None
    assert account.plan_type is None
    engine.dispose()


def test_refresh_status_failure_marks_account_failed(tmp_path):
    backend = FailingBackend()
    engine, session_factory, manager = make_manager(tmp_path, backend)

    try:
        manager.refresh_status(1)
    except RuntimeError:
        pass

    with session_factory() as session:
        account = session.get(Account, 1)

    assert account is not None
    assert account.auth_status == "auth_failed"
    engine.dispose()


def test_openai_codex_backend_isolates_subscription_auth_env(tmp_path):
    account = Account(
        name="Account A",
        slug="account-a",
        codex_home=str(tmp_path / "codex-home"),
        workspace_path=str(tmp_path / "workspace"),
    )

    config = OpenAiCodexAuthBackend()._config(account)

    assert config.cwd == str((tmp_path / "workspace").resolve())
    assert config.env["CODEX_HOME"] == str((tmp_path / "codex-home").resolve())
    assert config.env["CODEX_API_KEY"] == ""
    assert config.env["OPENAI_API_KEY"] == ""
