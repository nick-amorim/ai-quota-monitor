from __future__ import annotations

from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.models import Account, EventLog, UsageSnapshot
from ai_quota_monitor.services.accounts import archive_account, seed_defaults
from ai_quota_monitor.services.app_server import CodexAppServerNotification
from ai_quota_monitor.services.live_updates import RateLimitUpdateListener
from ai_quota_monitor.services.telemetry import TelemetryService


class UnusedTelemetryBackend:
    def read_account(self, account):
        return {"requiresOpenaiAuth": False}

    def read_rate_limits(self, account):
        raise AssertionError("direct polling not expected")


class FakeAppServerClient:
    instances = []

    def __init__(self, account, *, notification_handler):
        self.account = account
        self.notification_handler = notification_handler
        self.started = False
        self.initialized = False
        self.closed = False
        self.instances.append(self)

    def start(self):
        self.started = True

    def initialize(self):
        self.initialized = True
        return {"serverInfo": {"name": "fake"}}

    def close(self):
        self.closed = True


def make_listener(tmp_path):
    FakeAppServerClient.instances = []
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ai-quota-monitor.sqlite3'}",
        data_dir=tmp_path,
        enable_app_server_notifications=True,
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

    telemetry = TelemetryService(session_factory, lambda: UnusedTelemetryBackend())
    listener = RateLimitUpdateListener(
        session_factory,
        telemetry,
        settings,
        client_factory=FakeAppServerClient,
    )
    return engine, session_factory, listener


def test_rate_limit_listener_ingests_notifications_and_reloads_clients(tmp_path):
    engine, session_factory, listener = make_listener(tmp_path)

    try:
        listener.start()

        assert listener.active_account_ids == [1]
        client = FakeAppServerClient.instances[0]
        assert client.started is True
        assert client.initialized is True

        client.notification_handler(
            CodexAppServerNotification(
                method="account/rateLimits/updated",
                params={
                    "rateLimits": {
                        "primary": {
                            "usedPercent": 62,
                            "resetsAt": 1798797600,
                            "windowDurationMins": 300,
                        }
                    }
                },
            )
        )

        with session_factory() as session:
            snapshot = session.query(UsageSnapshot).one()
            events = session.query(EventLog).all()

        assert snapshot.account_id == 1
        assert snapshot.source == "codex-app-server-notification"
        assert snapshot.five_hour_used_percent == 62
        assert any(event.message == "Rate-limit notification ingested" for event in events)

        with session_factory() as session:
            account = session.get(Account, 1)
            assert account is not None
            account.auth_status = "not_configured"
            session.commit()

        listener.reload()

        assert listener.active_account_ids == []
        assert client.closed is True
    finally:
        listener.shutdown()
        engine.dispose()


def test_rate_limit_listener_skips_archived_accounts(tmp_path):
    engine, session_factory, listener = make_listener(tmp_path)

    try:
        with session_factory() as session:
            account = session.get(Account, 1)
            assert account is not None
            archive_account(session, account)

        listener.start()

        assert listener.active_account_ids == []
        assert FakeAppServerClient.instances == []
    finally:
        listener.shutdown()
        engine.dispose()
