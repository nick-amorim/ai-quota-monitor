from __future__ import annotations

from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates

from ai_quota_monitor.config import Settings
from ai_quota_monitor.main import create_app
from ai_quota_monitor.routes.dashboard import register_routes


def make_settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'ai-quota-monitor.sqlite3'}",
        data_dir=tmp_path,
    )


def test_health_reports_database_ok(tmp_path):
    app = create_app(make_settings(tmp_path))

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
    app = create_app(make_settings(tmp_path))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "ai-quota-monitor" in response.text
    assert "Accounts and schedules" in response.text
    assert "Account A" in response.text
    assert "Account B" in response.text
    assert "America/Recife" in response.text


def test_database_file_is_created(tmp_path):
    database_path = tmp_path / "ai-quota-monitor.sqlite3"
    app = create_app(
        Settings(
            database_url=f"sqlite:///{database_path}",
            data_dir=tmp_path,
        )
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

    app = create_app(settings)
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

    restarted_app = create_app(settings)
    with TestClient(restarted_app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'value="06:00"' in response.text
    assert 'value="friday"' in response.text
    assert 'value="America/Fortaleza"' in response.text
