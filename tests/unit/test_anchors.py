from __future__ import annotations

import threading

import pytest

from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.models import Account, AnchorRun
from ai_quota_monitor.services.accounts import seed_defaults
from ai_quota_monitor.services.anchors import (
    AnchorAccountNotReadyError,
    AnchorAlreadyRunningError,
    AnchorService,
    AnchorTurnResult,
    get_app_setting,
    update_app_setting,
)


class FakeAnchorBackend:
    calls = 0

    def run_anchor(self, account: Account, prompt: str) -> AnchorTurnResult:
        self.calls += 1
        return AnchorTurnResult(
            status="completed",
            thread_id=f"thread-{account.id}",
            turn_id=f"turn-{self.calls}",
            final_response="OK",
            token_usage={"input_tokens": 3, "output_tokens": 1},
            duration_ms=42,
        )


class BlockingAnchorBackend(FakeAnchorBackend):
    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        self.entered = entered
        self.release = release

    def run_anchor(self, account: Account, prompt: str) -> AnchorTurnResult:
        self.entered.set()
        self.release.wait(timeout=2)
        return super().run_anchor(account, prompt)


def make_service(tmp_path, backend):
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
        account = session.get(Account, 1)
        assert account is not None
        account.auth_status = "connected"
        session.commit()

    service = AnchorService(session_factory, settings, lambda: backend)
    return engine, session_factory, service


def test_manual_anchor_persists_successful_run(tmp_path):
    backend = FakeAnchorBackend()
    engine, session_factory, service = make_service(tmp_path, backend)

    run = service.run_manual_anchor(1)

    assert run.status == "completed"
    assert run.thread_id == "thread-1"
    assert run.turn_id == "turn-1"
    assert run.final_response == "OK"
    assert run.duration_ms == 42

    with session_factory() as session:
        persisted = session.get(AnchorRun, run.id)

    assert persisted is not None
    assert persisted.token_usage_json == '{"input_tokens": 3, "output_tokens": 1}'
    engine.dispose()


def test_manual_anchor_records_not_ready_failure(tmp_path):
    backend = FakeAnchorBackend()
    engine, session_factory, service = make_service(tmp_path, backend)

    with session_factory() as session:
        account = session.get(Account, 1)
        assert account is not None
        account.auth_status = "not_configured"
        session.commit()

    with pytest.raises(AnchorAccountNotReadyError):
        service.run_manual_anchor(1)

    with session_factory() as session:
        run = session.query(AnchorRun).one()

    assert run.status == "failed"
    assert "must be connected" in run.error_message
    assert backend.calls == 0
    engine.dispose()


def test_manual_anchor_blocks_concurrent_same_account_run(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    backend = BlockingAnchorBackend(entered, release)
    engine, session_factory, service = make_service(tmp_path, backend)
    errors = []

    first = threading.Thread(target=service.run_manual_anchor, args=(1,))
    first.start()
    assert entered.wait(timeout=2)

    with pytest.raises(AnchorAlreadyRunningError):
        service.run_manual_anchor(1)

    release.set()
    first.join(timeout=2)
    if first.is_alive():
        errors.append("first anchor thread did not finish")

    with session_factory() as session:
        runs = session.query(AnchorRun).all()

    assert errors == []
    assert len(runs) == 1
    assert runs[0].status == "completed"
    engine.dispose()


def test_anchor_prompt_setting_round_trips(tmp_path):
    backend = FakeAnchorBackend()
    engine, session_factory, service = make_service(tmp_path, backend)

    with session_factory() as session:
        update_app_setting(session, "anchor_prompt", "Reply only with PONG.")
        assert get_app_setting(session, "anchor_prompt") == "Reply only with PONG."

    run = service.run_manual_anchor(1)

    assert run.prompt == "Reply only with PONG."
    engine.dispose()
