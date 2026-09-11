from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.models import Account, AnchorRun, EventLog, UsageSnapshot
from ai_quota_monitor.services.accounts import seed_defaults
from ai_quota_monitor.services.anchors import AnchorService, AnchorTurnResult
from ai_quota_monitor.services.smart_anchors import SmartAnchorService
from ai_quota_monitor.services.telemetry import TelemetryService


class FakeAnchorBackend:
    calls = 0

    def run_anchor(self, account, prompt):
        self.calls += 1
        return AnchorTurnResult(
            status="completed",
            thread_id=f"thread-{account.id}",
            turn_id=f"turn-{self.calls}",
            final_response="OK",
            token_usage={"input_tokens": 1, "output_tokens": 1},
            duration_ms=10,
        )


class FakeTelemetryBackend:
    def __init__(self, payloads):
        self.payloads = list(payloads)

    def read_account(self, account):
        return {"requiresOpenaiAuth": False}

    def read_rate_limits(self, account):
        result = self.payloads.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def payload(*, five_hour_reset_at: datetime, weekly_reset_at: datetime) -> dict:
    return {
        "rateLimits": {
            "primary": {
                "usedPercent": 30,
                "resetsAt": int(five_hour_reset_at.timestamp()),
                "windowDurationMins": 300,
            },
            "secondary": {
                "usedPercent": 40,
                "resetsAt": int(weekly_reset_at.timestamp()),
                "windowDurationMins": 10080,
            },
        }
    }


def make_service(tmp_path, telemetry_backend, anchor_backend=None):
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

    if anchor_backend is None:
        anchor_backend = FakeAnchorBackend()

    anchor_service = AnchorService(session_factory, settings, lambda: anchor_backend)
    telemetry_service = TelemetryService(session_factory, lambda: telemetry_backend)
    service = SmartAnchorService(
        session_factory,
        settings,
        anchor_service,
        telemetry_service,
    )
    return engine, session_factory, service, anchor_backend


def test_scheduled_anchor_skips_when_five_hour_window_is_active(tmp_path):
    now = datetime.now(UTC)
    telemetry = FakeTelemetryBackend(
        [
            payload(
                five_hour_reset_at=now + timedelta(hours=2),
                weekly_reset_at=now + timedelta(days=3),
            )
        ]
    )
    engine, session_factory, service, anchor_backend = make_service(tmp_path, telemetry)

    try:
        result = service.run_scheduled_anchor(1, "daily")

        assert result.decision == "skipped_active_window"
        assert result.verification_status == "skipped"
        assert anchor_backend.calls == 0

        with session_factory() as session:
            assert session.query(AnchorRun).count() == 0
            event = (
                session.query(EventLog)
                .filter(EventLog.message.like("%skipped because 5-hour window is active"))
                .one()
            )
            assert event.account_id == 1
    finally:
        engine.dispose()


def test_scheduled_anchor_skips_when_account_anchors_are_paused(tmp_path):
    now = datetime.now(UTC)
    telemetry = FakeTelemetryBackend(
        [
            payload(
                five_hour_reset_at=now - timedelta(minutes=10),
                weekly_reset_at=now + timedelta(days=3),
            )
        ]
    )
    engine, session_factory, service, anchor_backend = make_service(tmp_path, telemetry)

    try:
        with session_factory() as session:
            account = session.get(Account, 1)
            assert account is not None
            assert account.schedule is not None
            account.schedule.anchor_paused = True
            session.commit()

        result = service.run_scheduled_anchor(1, "daily")

        assert result.decision == "skipped_paused"
        assert result.verification_status == "skipped"
        assert anchor_backend.calls == 0
        assert telemetry.payloads

        with session_factory() as session:
            assert session.query(AnchorRun).count() == 0
            event = (
                session.query(EventLog)
                .filter(EventLog.message.like("%skipped because anchors are paused"))
                .one()
            )
            assert event.account_id == 1
    finally:
        engine.dispose()


def test_scheduled_anchor_sends_and_verifies_observed_reset_move(tmp_path):
    now = datetime.now(UTC)
    telemetry = FakeTelemetryBackend(
        [
            payload(
                five_hour_reset_at=now - timedelta(minutes=10),
                weekly_reset_at=now + timedelta(days=1),
            ),
            payload(
                five_hour_reset_at=now + timedelta(hours=5),
                weekly_reset_at=now + timedelta(days=1),
            ),
        ]
    )
    engine, session_factory, service, anchor_backend = make_service(tmp_path, telemetry)

    try:
        result = service.run_scheduled_anchor(1, "daily")

        assert result.decision == "sent"
        assert result.verification_status == "verified"
        assert result.anchor_run_id is not None
        assert anchor_backend.calls == 1

        with session_factory() as session:
            assert session.query(AnchorRun).count() == 1
            snapshots = session.query(UsageSnapshot).order_by(UsageSnapshot.id).all()
            assert len(snapshots) == 2
            assert snapshots[-1].five_hour_observed_reset_at is not None
            assert snapshots[-1].five_hour_expected_reset_at is not None
    finally:
        engine.dispose()


def test_scheduled_anchor_honors_skip_setting_when_window_is_active(tmp_path):
    now = datetime.now(UTC)
    telemetry = FakeTelemetryBackend(
        [
            payload(
                five_hour_reset_at=now + timedelta(hours=2),
                weekly_reset_at=now + timedelta(days=1),
            ),
            payload(
                five_hour_reset_at=now + timedelta(hours=6),
                weekly_reset_at=now + timedelta(days=1),
            ),
        ]
    )
    engine, session_factory, service, anchor_backend = make_service(tmp_path, telemetry)

    try:
        with session_factory() as session:
            account = session.get(Account, 1)
            assert account is not None
            assert account.schedule is not None
            account.schedule.skip_if_window_active = False
            session.commit()

        result = service.run_scheduled_anchor(1, "daily")

        assert result.decision == "sent"
        assert result.verification_status == "verified"
        assert anchor_backend.calls == 1
    finally:
        engine.dispose()


def test_scheduled_anchor_marks_unchanged_observed_reset_unverified(tmp_path):
    now = datetime.now(UTC)
    reset_at = now - timedelta(minutes=5)
    telemetry = FakeTelemetryBackend(
        [
            payload(five_hour_reset_at=reset_at, weekly_reset_at=now + timedelta(days=1)),
            payload(five_hour_reset_at=reset_at, weekly_reset_at=now + timedelta(days=1)),
        ]
    )
    engine, session_factory, service, _ = make_service(tmp_path, telemetry)

    try:
        result = service.run_scheduled_anchor(1, "daily")

        assert result.decision == "sent"
        assert result.verification_status == "unchanged"

        with session_factory() as session:
            event = (
                session.query(EventLog)
                .filter(EventLog.message.like("%reset verification unchanged"))
                .one()
            )
            assert event.level == "warning"
    finally:
        engine.dispose()
