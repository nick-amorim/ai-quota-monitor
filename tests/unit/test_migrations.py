from __future__ import annotations

from ai_quota_monitor.migrations import migration_paths


def test_migration_paths_prefer_working_directory(monkeypatch, tmp_path):
    (tmp_path / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    (tmp_path / "alembic").mkdir()
    monkeypatch.chdir(tmp_path)

    alembic_ini, script_location = migration_paths()

    assert alembic_ini == tmp_path / "alembic.ini"
    assert script_location == tmp_path / "alembic"
