from __future__ import annotations

from pathlib import Path

from ai_quota_monitor.config import Settings
import ai_quota_monitor.services.system as system_module
from ai_quota_monitor.services.system import (
    CommandResult,
    SystemService,
    detect_deployment_mode,
    resolve_backup_dir,
    run_command,
    update_result_to_dict,
)


class FakeRunner:
    def __init__(
        self,
        *,
        dirty: bool = False,
        fail_command: str | None = None,
        fail_stderr: str = "failed",
        current_commit: str = "abc123",
        upstream_commit: str = "abc123",
        ancestor: bool = True,
    ):
        self.commands: list[list[str]] = []
        self.dirty = dirty
        self.fail_command = fail_command
        self.fail_stderr = fail_stderr
        self.current_commit = current_commit
        self.upstream_commit = upstream_commit
        self.ancestor = ancestor

    def __call__(self, command: list[str], cwd: Path | None):
        self.commands.append(command)
        if self.fail_command and self.fail_command in command:
            return CommandResult(command=command, returncode=1, stderr=self.fail_stderr)
        if command == ["git", "status", "--porcelain"]:
            stdout = " M README.md\n" if self.dirty else ""
            return CommandResult(command=command, returncode=0, stdout=stdout)
        if command == ["git", "status", "--porcelain", "--untracked-files=no"]:
            stdout = " M README.md\n" if self.dirty else ""
            return CommandResult(command=command, returncode=0, stdout=stdout)
        if command[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            return CommandResult(command=command, returncode=0, stdout="true\n")
        if command == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return CommandResult(command=command, returncode=0, stdout="main\n")
        if command == ["git", "rev-parse", "HEAD"]:
            return CommandResult(command=command, returncode=0, stdout=f"{self.current_commit}\n")
        if command == ["git", "rev-parse", "origin/main"]:
            return CommandResult(command=command, returncode=0, stdout=f"{self.upstream_commit}\n")
        if command[:2] == ["git", "merge-base"]:
            return CommandResult(
                command=command,
                returncode=0 if self.ancestor else 1,
            )
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
    assert result.steps[-1].status == "skipped"
    assert "restart helper" in result.steps[-1].detail


def test_check_update_reports_already_current_checkout(tmp_path):
    runner = FakeRunner(current_commit="abc123456", upstream_commit="abc123456")
    settings = make_settings(tmp_path, deployment_mode="proxmox")

    result = SystemService(settings, runner=runner).check_update()

    assert result.supported is True
    assert result.update_available is False
    assert result.can_update is False
    assert result.message == "Already up to date."
    assert ["git", "fetch", "--quiet", "origin", "main"] in runner.commands


def test_check_update_reports_available_clean_checkout(tmp_path):
    runner = FakeRunner(current_commit="abc123456", upstream_commit="def789000")
    settings = make_settings(tmp_path, deployment_mode="proxmox")

    result = SystemService(settings, runner=runner).check_update()

    assert result.update_available is True
    assert result.can_update is True
    assert result.current_commit == "abc12345"
    assert result.upstream_commit == "def78900"
    assert result.message == "Update available."


def test_check_update_blocks_dirty_tracked_checkout(tmp_path):
    runner = FakeRunner(
        dirty=True,
        current_commit="abc123456",
        upstream_commit="def789000",
    )
    settings = make_settings(tmp_path, deployment_mode="native")

    result = SystemService(settings, runner=runner).check_update()

    assert result.update_available is True
    assert result.can_update is False
    assert result.message == "Update available, but tracked local changes are present."


def test_check_update_blocks_diverged_checkout(tmp_path):
    runner = FakeRunner(
        current_commit="abc123456",
        upstream_commit="def789000",
        ancestor=False,
    )
    settings = make_settings(tmp_path, deployment_mode="native")

    result = SystemService(settings, runner=runner).check_update()

    assert result.update_available is True
    assert result.can_update is False
    assert "diverged" in result.message


def test_check_update_explains_git_object_permission_failure(tmp_path):
    runner = FakeRunner(
        fail_command="fetch",
        fail_stderr=(
            "error: insufficient permission for adding an object to repository "
            "database .git/objects\nfatal: failed to write object\n"
            "fatal: unpack-objects failed"
        ),
    )
    settings = make_settings(tmp_path, deployment_mode="proxmox")

    result = SystemService(settings, runner=runner).check_update()

    assert result.can_update is False
    assert result.message.startswith("Could not check for updates because")
    assert "chown -R aiquota:aiquota" in result.steps[-1].detail


def test_proxmox_web_update_uses_sudo_restart_helper(tmp_path, monkeypatch):
    helper_dir = tmp_path / "sbin"
    helper_dir.mkdir()
    helper = helper_dir / "ai-quota-monitor-restart"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    runner = FakeRunner()
    settings = make_settings(tmp_path, deployment_mode="proxmox")
    monkeypatch.setattr(system_module, "RESTART_HELPER_DIR", helper_dir)
    monkeypatch.setattr(system_module, "_is_root", lambda: False)
    monkeypatch.setattr(
        system_module.shutil,
        "which",
        lambda name: "/usr/bin/sudo" if name == "sudo" else None,
    )

    result = SystemService(settings, runner=runner).update(dry_run=True, restart=True)

    assert result.steps[-1].name == "restart"
    assert result.steps[-1].command == ["/usr/bin/sudo", "-n", str(helper)]


def test_root_proxmox_update_repairs_ownership_before_restart(tmp_path, monkeypatch):
    runner = FakeRunner()
    settings = make_settings(tmp_path, deployment_mode="proxmox")
    monkeypatch.setattr(system_module, "_is_root", lambda: True)
    monkeypatch.setattr(
        system_module.shutil,
        "which",
        lambda name: "/usr/bin/chown" if name == "chown" else None,
    )

    result = SystemService(settings, runner=runner).update(dry_run=True, restart=True)

    assert [step.name for step in result.steps] == [
        "backup",
        "worktree",
        "fetch",
        "merge",
        "install",
        "migrate",
        "ownership",
        "restart",
    ]
    assert result.steps[-2].command == [
        "/usr/bin/chown",
        "-R",
        "aiquota:aiquota",
        str(settings.install_dir),
        str(settings.data_dir),
    ]
    assert result.steps[-1].command == ["systemctl", "restart", "ai-quota-monitor"]


def test_non_root_proxmox_update_skips_missing_restart_helper(tmp_path, monkeypatch):
    runner = FakeRunner()
    settings = make_settings(tmp_path, deployment_mode="proxmox")
    monkeypatch.setattr(system_module, "_is_root", lambda: False)
    monkeypatch.setattr(system_module.shutil, "which", lambda name: None)

    result = SystemService(settings, runner=runner).update(dry_run=False, restart=True)

    assert result.changed is True
    assert result.message == (
        "Update completed. Restart the service manually to load the new version."
    )
    assert result.steps[-1].name == "restart"
    assert result.steps[-1].status == "skipped"
    assert "systemctl restart ai-quota-monitor" in result.steps[-1].detail
    assert ["systemctl", "restart", "ai-quota-monitor"] not in runner.commands


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
