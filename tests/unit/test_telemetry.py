from __future__ import annotations

from datetime import UTC, datetime

from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.models import Account, UsageRaw, UsageSnapshot
from ai_quota_monitor.services.accounts import seed_defaults
from ai_quota_monitor.services.telemetry import (
    TelemetryService,
    normalize_rate_limits,
)


def full_payload() -> dict:
    return {
        "rateLimits": {
            "primary": {
                "usedPercent": 72,
                "resetsAt": 1798797600,
                "windowDurationMins": 300,
            },
            "secondary": {
                "usedPercent": 43,
                "resetsAt": 1799110800,
                "windowDurationMins": 10080,
            },
        }
    }


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


class AccountSensitiveBackend:
    def read_account(self, account):
        return {"requiresOpenaiAuth": False}

    def read_rate_limits(self, account):
        if account.id == 1:
            raise RuntimeError("account a unavailable")
        return full_payload()


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

    service = TelemetryService(session_factory, lambda: backend)
    return engine, session_factory, service


def test_normalize_rate_limits_identifies_expected_windows_by_duration():
    normalized = normalize_rate_limits(full_payload())

    assert normalized.parser_status == "ok"
    assert normalized.five_hour is not None
    assert normalized.five_hour.used_percent == 72
    assert normalized.five_hour.window_minutes == 300
    assert normalized.five_hour.reset_at == datetime.fromtimestamp(1798797600, UTC)
    assert normalized.weekly is not None
    assert normalized.weekly.used_percent == 43
    assert normalized.weekly.window_minutes == 10080


def test_refresh_account_usage_persists_raw_and_normalized_snapshot(tmp_path):
    backend = FakeTelemetryBackend([full_payload()])
    engine, session_factory, service = make_service(tmp_path, backend)

    result = service.refresh_account_usage(1)

    assert result.status == "ok"
    assert result.snapshot is not None
    assert result.snapshot.five_hour_used_percent == 72
    assert result.snapshot.weekly_used_percent == 43
    assert _as_utc(result.snapshot.five_hour_observed_reset_at) == datetime.fromtimestamp(
        1798797600,
        UTC,
    )
    assert _as_utc(result.snapshot.weekly_observed_reset_at) == datetime.fromtimestamp(
        1799110800,
        UTC,
    )
    assert result.snapshot.five_hour_expected_reset_at is not None
    assert result.snapshot.weekly_expected_reset_at is not None

    with session_factory() as session:
        raw = session.query(UsageRaw).one()
        snapshot = session.query(UsageSnapshot).one()

    assert '"usedPercent": 72' in raw.payload_json
    assert snapshot.raw_usage_id == raw.id
    engine.dispose()


def test_sparse_payload_preserves_previous_known_values(tmp_path):
    first = full_payload()
    sparse = {
        "rateLimits": {
            "secondary": {
                "usedPercent": 44,
                "resetsAt": 1799114400,
                "windowDurationMins": 10080,
            }
        }
    }
    backend = FakeTelemetryBackend([first, sparse])
    engine, session_factory, service = make_service(tmp_path, backend)

    service.refresh_account_usage(1)
    result = service.refresh_account_usage(1)

    assert result.snapshot is not None
    assert result.status == "partial"
    assert result.snapshot.five_hour_used_percent == 72
    assert result.snapshot.five_hour_window_minutes == 300
    assert result.snapshot.weekly_used_percent == 44

    with session_factory() as session:
        assert session.query(UsageRaw).count() == 2
        assert session.query(UsageSnapshot).count() == 2
    engine.dispose()


def test_notification_sparse_update_preserves_previous_snapshot(tmp_path):
    backend = FakeTelemetryBackend([full_payload()])
    engine, session_factory, service = make_service(tmp_path, backend)

    service.refresh_account_usage(1)
    result = service.record_rate_limit_update(
        1,
        {
            "rateLimits": {
                "primary": {
                    "usedPercent": 73,
                    "resetsAt": 1798799400,
                    "windowDurationMins": 300,
                }
            }
        },
    )

    assert result.snapshot is not None
    assert result.status == "partial"
    assert result.snapshot.source == "codex-app-server-notification"
    assert result.snapshot.five_hour_used_percent == 73
    assert result.snapshot.weekly_used_percent == 43
    assert result.snapshot.weekly_window_minutes == 10080

    with session_factory() as session:
        assert session.query(UsageRaw).count() == 2
        assert session.query(UsageSnapshot).count() == 2
    engine.dispose()


def test_refresh_all_accounts_isolates_backend_failure(tmp_path):
    backend = AccountSensitiveBackend()
    engine, session_factory, service = make_service(tmp_path, backend)

    results = service.refresh_all_accounts()

    assert [(result.account_id, result.status) for result in results] == [
        (1, "failed"),
        (2, "ok"),
    ]

    with session_factory() as session:
        assert session.query(UsageRaw).count() == 1
        assert session.query(UsageSnapshot).count() == 1
    engine.dispose()


def test_refresh_connected_accounts_skips_disconnected_accounts(tmp_path):
    backend = AccountSensitiveBackend()
    engine, session_factory, service = make_service(tmp_path, backend)

    with session_factory() as session:
        account = session.get(Account, 1)
        assert account is not None
        account.auth_status = "not_configured"
        account = session.get(Account, 2)
        assert account is not None
        account.auth_status = "connected"
        session.commit()

    results = service.refresh_connected_accounts()

    assert [(result.account_id, result.status) for result in results] == [(2, "ok")]
    engine.dispose()


def test_refresh_failure_records_visible_error_event(tmp_path):
    backend = FakeTelemetryBackend([FileNotFoundError("No such file or directory: 'codex'")])
    engine, session_factory, service = make_service(tmp_path, backend)

    result = service.refresh_account_usage(1)
    errors = service.latest_refresh_errors_by_account()

    assert result.status == "failed"
    assert "Codex CLI is not available" in (result.error or "")
    assert errors[1] == result.error

    with session_factory() as session:
        assert session.query(UsageRaw).count() == 0
    engine.dispose()


def test_successful_refresh_hides_older_refresh_error(tmp_path):
    backend = FakeTelemetryBackend([
        RuntimeError("temporary telemetry failure"),
        full_payload(),
    ])
    engine, _session_factory, service = make_service(tmp_path, backend)

    service.refresh_account_usage(1)
    assert service.latest_refresh_errors_by_account()[1] == "temporary telemetry failure"

    service.refresh_account_usage(1)

    assert service.latest_refresh_errors_by_account() == {}
    engine.dispose()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
