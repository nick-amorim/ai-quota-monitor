from __future__ import annotations

from pathlib import Path

from ai_quota_monitor.config import Settings
from ai_quota_monitor.services.system import (
    CommandResult,
    SystemService,
    detect_deployment_mode,
    resolve_backup_dir,
    run_command,
    update_result_to_dict,
)


class FakeRunner:
    def __init__(self, *, dirty: bool = False, fail_command: str | None = None):
        self.commands: list[list[str]] = []
        self.dirty = dirty
        self.fail_command = fail_command

    def __call__(self, command: list[str], cwd: Path | None):
        self.commands.append(command)
        if self.fail_command and self.fail_command in command:
            return CommandResult(command=command, returncode=1, stderr="failed")
        if command == ["git", "status", "--porcelain"]:
            stdout = " M README.md\n" if self.dirty else ""
            return CommandResult(command=command, returncode=0, stdout=stdout)
        if command[:2] == ["git", "rev-parse"]:
            return CommandResult(command=command, returncode=0, stdout="abc123\n")
        return CommandResult(command=command, returncode=0, stdout="ok\n")


def make_settings(tmp_path, **overrides):
    values = {
        "database_url": f"sqlite:///{tmp_path / 'ai-quota-monitor.sqlite3'}",
        "data_dir": tmp_path,
        "install_dir": tmp_path / "checkout",
    }
    values.update(overrides)
    return Settings(**values)


def test_system_info_reports_docker_update_guidance(tmp_path):
    settings = make_settings(tmp_path, deployment_mode="docker")
    service = SystemService(settings, runner=FakeRunner())

    info = service.info()

    assert info.deployment_mode == "docker"
    assert info.update_supported is False
    assert "Docker Compose" in info.update_message


def test_update_dry_run_plans_backup_and_shared_update_commands(tmp_path):
    runner = FakeRunner()
    settings = make_settings(tmp_path, deployment_mode="proxmox")
    result = SystemService(settings, runner=runner).update(dry_run=True, restart=True)

    assert result.supported is True
    assert result.dry_run is True
    assert [step.name for step in result.steps] == [
        "backup",
        "worktree",
        "fetch",
        "merge",
        "install",
        "migrate",
        "restart",
    ]
    assert ["git", "status", "--porcelain"] in runner.commands
    assert result.steps[-1].command == ["systemctl", "restart", "ai-quota-monitor"]


def test_update_refuses_dirty_checkout_before_running_commands(tmp_path):
    runner = FakeRunner(dirty=True)
    settings = make_settings(tmp_path, deployment_mode="native")
    result = SystemService(settings, runner=runner).update(dry_run=False)

    assert result.changed is False
    assert result.message == "Update refused because the checkout is dirty."
    assert [step.name for step in result.steps] == ["backup", "worktree"]
    assert not any(command[:2] == ["git", "merge"] for command in runner.commands)


def test_update_copies_sqlite_database_before_migrations(tmp_path):
    database_path = tmp_path / "ai-quota-monitor.sqlite3"
    database_path.write_text("database contents", encoding="utf-8")
    settings = make_settings(tmp_path, deployment_mode="native")

    result = SystemService(settings, runner=FakeRunner()).update(dry_run=False)

    backups = list((tmp_path / "backups").glob("ai-quota-monitor-*.sqlite3"))
    assert result.changed is True
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "database contents"


def test_docker_update_returns_compose_message_without_commands(tmp_path):
    runner = FakeRunner()
    settings = make_settings(tmp_path, deployment_mode="docker")
    result = SystemService(settings, runner=runner).update(dry_run=False)

    assert result.supported is False
    assert "Compose" in result.message
    assert runner.commands == []


def test_backup_dir_relative_to_data_dir(tmp_path):
    settings = make_settings(tmp_path, backup_dir=Path("snapshots"))

    assert resolve_backup_dir(settings) == tmp_path / "snapshots"


def test_update_result_serializes_for_api():
    result = SystemService(Settings(deployment_mode="docker"), runner=FakeRunner()).update()

    payload = update_result_to_dict(result)

    assert payload["deployment_mode"] == "docker"
    assert payload["supported"] is False


def test_explicit_deployment_mode_wins(tmp_path):
    settings = make_settings(tmp_path, deployment_mode="proxmox")

    assert detect_deployment_mode(settings) == "proxmox"


def test_missing_command_returns_failed_result(tmp_path):
    result = run_command(["definitely-not-ai-quota-monitor-command"], tmp_path)

    assert result.returncode == 127
    assert result.stderr
