from __future__ import annotations

from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates

from ai_quota_monitor.config import Settings
from ai_quota_monitor.database import create_database_engine, create_session_factory
from ai_quota_monitor.main import create_app
from ai_quota_monitor.models import Account
from ai_quota_monitor.routes.dashboard import register_routes
from ai_quota_monitor.services.anchors import AnchorTurnResult
from ai_quota_monitor.services.codex_auth import CodexAccountInfo
from ai_quota_monitor.services.events import record_event
from ai_quota_monitor.services.system import SystemInfo, UpdateResult, UpdateStep


def make_settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'ai-quota-monitor.sqlite3'}",
        data_dir=tmp_path,
        enable_app_server_notifications=False,
    )


class FakeAuthBackend:
    def start_device_login(self, account):
        raise AssertionError("device login not expected")

    def read_account(self, account):
        return CodexAccountInfo(
            external_id="user@example.test",
            display="user@example.test",
            plan_type="plus",
            is_authenticated=True,
        )

    def logout(self, account):
        return None

    def close_login_attempt(self, attempt):
        return None


class FailingAuthBackend(FakeAuthBackend):
    def read_account(self, account):
        raise RuntimeError("codex unavailable")


class FakeAnchorBackend:
    def run_anchor(self, account, prompt):
        return AnchorTurnResult(
            status="completed",
            thread_id="thread-1",
            turn_id="turn-1",
            final_response="OK",
            token_usage={"input_tokens": 3, "output_tokens": 1},
            duration_ms=25,
        )


class FakeTelemetryBackend:
    def read_account(self, account):
        return {"requiresOpenaiAuth": False}

    def read_rate_limits(self, account):
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


class FakeSystemService:
    def __init__(self, settings):
        self.settings = settings

    def info(self):
        return SystemInfo(
            app_name="ai-quota-monitor",
            deployment_mode=(
                self.settings.deployment_mode
                if self.settings.deployment_mode != "auto"
                else "native"
            ),
            update_supported=True,
            update_message="CLI updates are available.",
            web_updates_enabled=self.settings.enable_web_updates,
            current_branch="main",
            current_commit="abc123",
            upstream_commit="def456",
            dirty=False,
            install_dir=str(self.settings.install_dir),
            data_dir=str(self.settings.data_dir),
            database_path=str(self.settings.data_dir / "ai-quota-monitor.sqlite3"),
            backup_dir=str(self.settings.data_dir / "backups"),
        )

    def update(self, *, dry_run=True, restart=False):
        return UpdateResult(
            deployment_mode="native",
            supported=True,
            dry_run=dry_run,
            changed=not dry_run,
            message="Update plan is ready." if dry_run else "Update completed.",
            steps=[
                UpdateStep(
                    name="backup",
                    status="planned" if dry_run else "completed",
                    detail="Copy database before migrations.",
                ),
                UpdateStep(
                    name="migrate",
                    status="planned" if dry_run else "completed",
                    detail="Run Alembic migrations.",
                    command=["python", "-m", "alembic", "upgrade", "head"],
                ),
            ],
        )


def make_app(tmp_path):
    return create_app(
        make_settings(tmp_path),
        auth_backend_factory=FakeAuthBackend,
        anchor_backend_factory=FakeAnchorBackend,
        telemetry_backend_factory=FakeTelemetryBackend,
        system_service_factory=FakeSystemService,
    )


def mark_account_connected(settings, account_id: int = 1) -> None:
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        account = session.get(Account, account_id)
        assert account is not None
        account.auth_status = "connected"
        session.commit()
    engine.dispose()


def test_health_reports_database_ok(tmp_path):
    app = make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "ai-quota-monitor",
        "version": "0.1.0",
        "database": "ok",
    }


def test_dashboard_shell_renders(tmp_path):
    app = make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert '<html lang="en" class="dark">' in response.text
    assert "ai-quota-monitor" in response.text
    assert "Accounts and schedules" in response.text
    assert "Account A" in response.text
    assert "Account B" in response.text
    assert "Start device login" in response.text
    assert "Check status" in response.text
    assert "Run anchor" in response.text
    assert "Global anchor prompt" in response.text
    assert "Scheduled anchors" in response.text
    assert "Timeline" in response.text
    assert "Deployment and updates" in response.text
    assert "Check update plan" in response.text
    assert 'data-theme-toggle' in response.text
    assert 'data-drawer-open="settings-drawer"' in response.text
    assert 'id="settings-drawer"' in response.text
    assert 'href="/monitor"' in response.text
    assert 'href="/history"' in response.text
    assert 'hx-get="/partials/scheduler"' in response.text
    assert 'hx-get="/partials/events/recent"' in response.text
    assert "No anchor runs yet." in response.text
    assert "America/Recife" in response.text


def test_database_file_is_created(tmp_path):
    database_path = tmp_path / "ai-quota-monitor.sqlite3"
    app = create_app(
        Settings(
            database_url=f"sqlite:///{database_path}",
            data_dir=tmp_path,
        ),
        auth_backend_factory=FakeAuthBackend,
    )

    with TestClient(app):
        pass

    assert database_path.exists()


def test_app_factory_does_not_reuse_dashboard_routes(tmp_path):
    templates = Jinja2Templates(directory="src/ai_quota_monitor/templates")
    first_router = register_routes(templates)
    second_router = register_routes(templates)

    assert first_router is not second_router
    assert sum(route.path == "/" for route in first_router.routes) == 1
    assert sum(route.path == "/" for route in second_router.routes) == 1


def test_schedule_update_persists_after_restart(tmp_path):
    database_path = tmp_path / "ai-quota-monitor.sqlite3"
    settings = Settings(
        database_url=f"sqlite:///{database_path}",
        data_dir=tmp_path,
    )

    app = create_app(
        settings,
        auth_backend_factory=FakeAuthBackend,
        anchor_backend_factory=FakeAnchorBackend,
        telemetry_backend_factory=FakeTelemetryBackend,
    )
    with TestClient(app) as client:
        response = client.post(
            "/accounts/1/schedule",
            data={
                "enabled": "on",
                "daily_anchor_enabled": "on",
                "daily_anchor_time": "06:00",
                "weekly_target_day": "friday",
                "weekly_target_time": "07:00",
                "timezone": "America/Fortaleza",
                "monday_enabled": "on",
                "friday_enabled": "on",
                "skip_if_window_active": "on",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303

    restarted_app = create_app(settings, auth_backend_factory=FakeAuthBackend)
    with TestClient(restarted_app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'value="06:00"' in response.text
    assert 'value="friday"' in response.text
    assert 'value="America/Fortaleza"' in response.text
    assert "Scheduled anchors" in response.text


def test_auth_status_route_updates_account_metadata(tmp_path):
    app = make_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/accounts/1/auth/status",
            follow_redirects=False,
        )
        dashboard = client.get("/")

    assert response.status_code == 303
    assert "user@example.test" in dashboard.text
    assert "plus" in dashboard.text


def test_auth_status_route_records_failure_without_error_page(tmp_path):
    app = create_app(make_settings(tmp_path), auth_backend_factory=FailingAuthBackend)

    with TestClient(app) as client:
        response = client.post(
            "/accounts/1/auth/status",
            follow_redirects=False,
        )
        dashboard = client.get("/")

    assert response.status_code == 303
    assert "Auth Failed" in dashboard.text


def test_anchor_prompt_update_persists(tmp_path):
    app = make_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/settings/anchor",
            data={"anchor_prompt": "Reply with PONG only."},
            follow_redirects=False,
        )
        dashboard = client.get("/")

    assert response.status_code == 303
    assert "Reply with PONG only." in dashboard.text


def test_manual_anchor_route_records_history(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(
        settings,
        auth_backend_factory=FakeAuthBackend,
        anchor_backend_factory=FakeAnchorBackend,
        telemetry_backend_factory=FakeTelemetryBackend,
    )

    with TestClient(app) as client:
        mark_account_connected(settings)
        response = client.post(
            "/accounts/1/anchors/run",
            follow_redirects=False,
        )
        dashboard = client.get("/")

    assert response.status_code == 303
    assert "Recent anchor runs" in dashboard.text
    assert "Completed" in dashboard.text
    assert "OK" in dashboard.text


def test_usage_refresh_route_records_quota_snapshot(tmp_path):
    app = make_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/accounts/1/usage/refresh",
            follow_redirects=False,
        )
        dashboard = client.get("/")

    assert response.status_code == 303
    assert "Quota telemetry" in dashboard.text
    assert "Observed reset" in dashboard.text
    assert "Expected reset" in dashboard.text
    assert "Configured:" in dashboard.text
    assert "Weekly drift" in dashboard.text
    assert "72" in dashboard.text
    assert "43" in dashboard.text


def test_history_route_filters_events(tmp_path):
    app = make_app(tmp_path)

    with TestClient(app) as client:
        with app.state.session_factory() as session:
            record_event(
                session,
                level="info",
                category="scheduler.reload",
                message="Scheduler reloaded",
                account_id=1,
            )
            record_event(
                session,
                level="warning",
                category="live-updates",
                message="Listener start failed",
                account_id=2,
            )

        response = client.get("/history?account_id=2&level=warning")

    assert response.status_code == 200
    assert "Event history" in response.text
    assert "Listener start failed" in response.text
    assert "Scheduler reloaded" not in response.text
    assert "All levels" in response.text


def test_partial_routes_render_refreshable_sections(tmp_path):
    app = make_app(tmp_path)

    with TestClient(app) as client:
        client.post("/accounts/1/usage/refresh", follow_redirects=False)
        usage = client.get("/partials/accounts/1/usage")
        scheduler = client.get("/partials/scheduler")
        events = client.get("/partials/events/recent")

    assert usage.status_code == 200
    assert "Quota telemetry" in usage.text
    assert "72" in usage.text
    assert scheduler.status_code == 200
    assert "Scheduled anchors" in scheduler.text
    assert events.status_code == 200
    assert "Recent events" in events.text


def test_monitor_route_renders_compact_quota_view(tmp_path):
    app = make_app(tmp_path)

    with TestClient(app) as client:
        client.post("/accounts/1/usage/refresh", follow_redirects=False)
        response = client.get("/monitor")

    assert response.status_code == 200
    assert '<html lang="en" class="dark">' in response.text
    assert "Quota monitor" in response.text
    assert 'class="monitor-body"' in response.text
    assert 'hx-get="/partials/monitor"' in response.text
    assert "Account A" in response.text
    assert "5-hour" in response.text
    assert "Weekly drift" in response.text


def test_account_monitor_and_partial_filter_to_slug(tmp_path):
    app = make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/monitor/account-a")
        partial = client.get("/partials/monitor/account-a")
        missing = client.get("/monitor/not-real")

    assert response.status_code == 200
    assert partial.status_code == 200
    assert missing.status_code == 404
    assert "Account A" in response.text
    assert "Account B" not in response.text
    assert "Account A" in partial.text
    assert "Account B" not in partial.text


def test_system_api_reports_update_status(tmp_path):
    app = make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/system/info")

    assert response.status_code == 200
    assert response.json()["app_name"] == "ai-quota-monitor"
    assert response.json()["deployment_mode"] == "native"
    assert response.json()["backup_dir"].endswith("backups")


def test_system_update_api_allows_dry_run_but_blocks_real_web_update(tmp_path):
    app = make_app(tmp_path)

    with TestClient(app) as client:
        dry_run = client.post("/api/system/update", data={"dry_run": "true"})
        real_update = client.post("/api/system/update", data={"dry_run": "false"})

    assert dry_run.status_code == 200
    assert dry_run.json()["dry_run"] is True
    assert dry_run.json()["steps"][0]["name"] == "backup"
    assert real_update.status_code == 403
