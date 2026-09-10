from __future__ import annotations

import os
from argparse import Namespace

from ai_quota_monitor.config import get_settings
from ai_quota_monitor.update import _load_env_file, _settings_from_args


def test_updater_loads_production_environment_before_settings(tmp_path, monkeypatch):
    env_file = tmp_path / "ai-quota-monitor.env"
    data_dir = tmp_path / "runtime"
    install_dir = tmp_path / "checkout"
    env_file.write_text(
        "\n".join(
            [
                "AI_QUOTA_MONITOR_ENV=production",
                f"AI_QUOTA_MONITOR_DATABASE_URL=sqlite:///{data_dir}/ai-quota-monitor.sqlite3",
                f"AI_QUOTA_MONITOR_DATA_DIR={data_dir}",
                f"AI_QUOTA_MONITOR_INSTALL_DIR={install_dir}",
                "AI_QUOTA_MONITOR_DEPLOYMENT_MODE=proxmox",
            ]
        ),
        encoding="utf-8",
    )
    for key in (
        "AI_QUOTA_MONITOR_ENV",
        "AI_QUOTA_MONITOR_DATABASE_URL",
        "AI_QUOTA_MONITOR_DATA_DIR",
        "AI_QUOTA_MONITOR_INSTALL_DIR",
        "AI_QUOTA_MONITOR_DEPLOYMENT_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()

    try:
        _load_env_file(env_file)
        settings = _settings_from_args(
            Namespace(
                install_dir=None,
                data_dir=None,
                backup_dir=None,
                deployment_mode=None,
            )
        )

        assert settings.env == "production"
        assert settings.database_url == f"sqlite:///{data_dir}/ai-quota-monitor.sqlite3"
        assert settings.data_dir == data_dir
        assert settings.install_dir == install_dir
        assert settings.deployment_mode == "proxmox"
    finally:
        for key in (
            "AI_QUOTA_MONITOR_ENV",
            "AI_QUOTA_MONITOR_DATABASE_URL",
            "AI_QUOTA_MONITOR_DATA_DIR",
            "AI_QUOTA_MONITOR_INSTALL_DIR",
            "AI_QUOTA_MONITOR_DEPLOYMENT_MODE",
            "AI_QUOTA_MONITOR_ENV_FILE",
        ):
            os.environ.pop(key, None)
        get_settings.cache_clear()
