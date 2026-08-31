from __future__ import annotations

from fastapi.testclient import TestClient

from ai_quota_monitor.config import Settings
from ai_quota_monitor.main import create_app


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
    assert "Dashboard shell" in response.text
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
