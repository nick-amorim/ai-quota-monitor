from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from ai_quota_monitor.config import Settings


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class UpdateStep:
    name: str
    status: str
    detail: str
    command: list[str] | None = None


@dataclass(frozen=True)
class SystemInfo:
    app_name: str
    deployment_mode: str
    update_supported: bool
    update_message: str
    web_updates_enabled: bool
    current_branch: str | None
    current_commit: str | None
    upstream_commit: str | None
    dirty: bool | None
    install_dir: str
    data_dir: str
    database_path: str | None
    backup_dir: str


@dataclass(frozen=True)
class UpdateResult:
    deployment_mode: str
    supported: bool
    dry_run: bool
    changed: bool
    message: str
    steps: list[UpdateStep]
    update_available: bool | None = None
    can_update: bool | None = None
    current_branch: str | None = None
    current_commit: str | None = None
    upstream_commit: str | None = None


CommandRunner = Callable[[list[str], Path | None], CommandResult]
RESTART_HELPER_DIR = Path("/usr/local/sbin")
PROXMOX_SERVICE_OWNER = "aiquota:aiquota"


class SystemService:
    def __init__(
        self,
        settings: Settings,
        *,
        runner: CommandRunner | None = None,
    ) -> None:
        self.settings = settings
        self.runner = runner or run_command

    def info(self) -> SystemInfo:
        deployment_mode = detect_deployment_mode(self.settings)
        current_branch = self._git_value(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        current_commit = self._git_value(["git", "rev-parse", "--short", "HEAD"])
        upstream_commit = self._git_value(
            [
                "git",
                "rev-parse",
                "--short",
                f"{self.settings.update_remote}/{self.settings.update_branch}",
            ],
        )
        dirty_result = self.runner(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            self._cwd(),
        )
        dirty = (
            bool(dirty_result.stdout.strip())
            if dirty_result.returncode == 0
            else None
        )
        update_supported, update_message = update_support(
            deployment_mode,
            web_updates_enabled=self.settings.enable_web_updates,
        )

        return SystemInfo(
            app_name=self.settings.app_name,
            deployment_mode=deployment_mode,
            update_supported=update_supported,
            update_message=update_message,
            web_updates_enabled=self.settings.enable_web_updates,
            current_branch=current_branch,
            current_commit=current_commit,
            upstream_commit=upstream_commit,
            dirty=dirty,
            install_dir=str(self.settings.install_dir),
            data_dir=str(self.settings.data_dir),
            database_path=_database_path(self.settings.database_url),
            backup_dir=str(resolve_backup_dir(self.settings)),
        )

    def update(
        self,
        *,
        dry_run: bool = True,
        restart: bool = False,
    ) -> UpdateResult:
        deployment_mode = detect_deployment_mode(self.settings)
        if deployment_mode == "docker":
            return UpdateResult(
                deployment_mode=deployment_mode,
                supported=False,
                dry_run=dry_run,
                changed=False,
                message=(
                    "Docker deployments should be updated by pulling a new image "
                    "and recreating the Compose service."
                ),
                steps=[],
            )

        steps: list[UpdateStep] = []
        commands = self._update_commands()
        ownership_command = _ownership_repair_command(self.settings, deployment_mode)
        if ownership_command:
            commands.append(ownership_command)
        restart_command = _restart_command(self.settings, deployment_mode) if restart else None
        if restart:
            if restart_command:
                commands.append(restart_command)

        database_path = _database_path(self.settings.database_url)
        backup_path = _backup_path(self.settings)
        steps.append(
            UpdateStep(
                name="backup",
                status="planned" if dry_run else "completed",
                detail=(
                    f"Copy {database_path} to {backup_path}"
                    if database_path
                    else "No SQLite database path detected; backup skipped"
                ),
            ),
        )
        if not dry_run and database_path:
            source = Path(database_path)
            if source.exists():
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, backup_path)
            else:
                steps[-1] = UpdateStep(
                    name="backup",
                    status="skipped",
                    detail=f"Database file does not exist yet: {database_path}",
                )

        dirty_result = self.runner(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            self._cwd(),
        )
        if dirty_result.returncode != 0:
            steps.append(
                UpdateStep(
                    name="worktree",
                    status="failed",
                    detail=_command_error(dirty_result),
                    command=dirty_result.command,
                ),
            )
            return UpdateResult(
                deployment_mode=deployment_mode,
                supported=True,
                dry_run=dry_run,
                changed=False,
                message="Unable to inspect the Git worktree.",
                steps=steps,
            )
        changed_files = _changed_files(dirty_result.stdout)
        if changed_files:
            detail = (
                "The application checkout has tracked local changes: "
                f"{', '.join(changed_files)}."
            )
            steps.append(
                UpdateStep(
                    name="worktree",
                    status="failed",
                    detail=detail,
                    command=dirty_result.command,
                ),
            )
            return UpdateResult(
                deployment_mode=deployment_mode,
                supported=True,
                dry_run=dry_run,
                changed=False,
                message="Update refused because the checkout is dirty.",
                steps=steps,
            )

        steps.append(
            UpdateStep(
                name="worktree",
                status="completed",
                detail="Git checkout is clean.",
                command=dirty_result.command,
            ),
        )

        if dry_run:
            steps.extend(
                UpdateStep(
                    name=_step_name(command),
                    status="planned",
                    detail=" ".join(command),
                    command=command,
                )
                for command in commands
            )
            if restart and restart_command is None:
                steps.append(
                    UpdateStep(
                        name="restart",
                        status="skipped",
                        detail=_restart_unavailable_message(self.settings),
                    )
                )
            return UpdateResult(
                deployment_mode=deployment_mode,
                supported=True,
                dry_run=True,
                changed=False,
                message="Update plan is ready.",
                steps=steps,
            )

        changed = False
        for command in commands:
            result = self.runner(command, self._cwd())
            if result.returncode != 0:
                steps.append(
                    UpdateStep(
                        name=_step_name(command),
                        status="failed",
                        detail=_command_error(result),
                        command=result.command,
                    ),
                )
                return UpdateResult(
                    deployment_mode=deployment_mode,
                    supported=True,
                    dry_run=False,
                    changed=changed,
                    message=_command_failure_message(command),
                    steps=steps,
                )
            changed = True
            steps.append(
                UpdateStep(
                    name=_step_name(command),
                    status="completed",
                    detail=(result.stdout or result.stderr or "Command completed.").strip(),
                    command=result.command,
                )
            )

        if restart and restart_command is None:
            steps.append(
                UpdateStep(
                    name="restart",
                    status="skipped",
                    detail=_restart_unavailable_message(self.settings),
                )
            )
            return UpdateResult(
                deployment_mode=deployment_mode,
                supported=True,
                dry_run=False,
                changed=changed,
                message=(
                    "Update completed. Restart the service manually to load "
                    "the new version."
                ),
                steps=steps,
            )

        return UpdateResult(
            deployment_mode=deployment_mode,
            supported=True,
            dry_run=False,
            changed=changed,
            message="Update completed.",
            steps=steps,
        )

    def check_update(self) -> UpdateResult:
        deployment_mode = detect_deployment_mode(self.settings)
        if deployment_mode == "docker":
            return UpdateResult(
                deployment_mode=deployment_mode,
                supported=False,
                dry_run=True,
                changed=False,
                message=(
                    "Docker deployments update through Docker Compose, not "
                    "dashboard installation."
                ),
                steps=[],
                update_available=False,
                can_update=False,
            )

        steps: list[UpdateStep] = []
        remote_ref = f"{self.settings.update_remote}/{self.settings.update_branch}"

        inside_result = self.runner(
            ["git", "rev-parse", "--is-inside-work-tree"],
            self._cwd(),
        )
        if inside_result.returncode != 0:
            steps.append(
                UpdateStep(
                    name="git",
                    status="failed",
                    detail=_command_error(inside_result),
                    command=inside_result.command,
                )
            )
            return UpdateResult(
                deployment_mode=deployment_mode,
                supported=False,
                dry_run=True,
                changed=False,
                message="This install is not a Git checkout.",
                steps=steps,
                update_available=False,
                can_update=False,
            )
        steps.append(
            UpdateStep(
                name="git",
                status="completed",
                detail="Git checkout detected.",
                command=inside_result.command,
            )
        )

        fetch_result = self.runner(
            [
                "git",
                "fetch",
                "--quiet",
                self.settings.update_remote,
                self.settings.update_branch,
            ],
            self._cwd(),
        )
        if fetch_result.returncode != 0:
            message = _fetch_failure_message(fetch_result)
            steps.append(
                UpdateStep(
                    name="fetch",
                    status="failed",
                    detail=message,
                    command=fetch_result.command,
                )
            )
            return UpdateResult(
                deployment_mode=deployment_mode,
                supported=True,
                dry_run=True,
                changed=False,
                message=message,
                steps=steps,
                update_available=None,
                can_update=False,
            )
        steps.append(
            UpdateStep(
                name="fetch",
                status="completed",
                detail=f"Fetched {self.settings.update_remote}/{self.settings.update_branch}.",
                command=fetch_result.command,
            )
        )

        current_branch = self._git_value(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        current_commit = self._git_value(["git", "rev-parse", "HEAD"])
        upstream_commit = self._git_value(["git", "rev-parse", remote_ref])
        if not current_commit or not upstream_commit:
            steps.append(
                UpdateStep(
                    name="compare",
                    status="failed",
                    detail=f"Unable to compare HEAD with {remote_ref}.",
                )
            )
            return UpdateResult(
                deployment_mode=deployment_mode,
                supported=True,
                dry_run=True,
                changed=False,
                message="Could not compare local and upstream versions.",
                steps=steps,
                update_available=None,
                can_update=False,
                current_branch=current_branch,
                current_commit=_short_commit(current_commit),
                upstream_commit=_short_commit(upstream_commit),
            )

        dirty_result = self.runner(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            self._cwd(),
        )
        if dirty_result.returncode != 0:
            steps.append(
                UpdateStep(
                    name="worktree",
                    status="failed",
                    detail=_command_error(dirty_result),
                    command=dirty_result.command,
                )
            )
            return UpdateResult(
                deployment_mode=deployment_mode,
                supported=True,
                dry_run=True,
                changed=False,
                message="Unable to inspect the Git worktree.",
                steps=steps,
                update_available=None,
                can_update=False,
                current_branch=current_branch,
                current_commit=_short_commit(current_commit),
                upstream_commit=_short_commit(upstream_commit),
            )

        changed_files = _changed_files(dirty_result.stdout)
        dirty = bool(changed_files)
        ancestor_result = self.runner(
            ["git", "merge-base", "--is-ancestor", current_commit, upstream_commit],
            self._cwd(),
        )
        update_available = current_commit != upstream_commit
        diverged = bool(update_available and ancestor_result.returncode != 0)
        can_update = bool(update_available and not dirty and not diverged)

        if not update_available:
            message = "Already up to date."
        elif dirty:
            message = "Update available, but tracked local changes are present."
        elif diverged:
            message = (
                "Update available, but the local checkout has diverged from "
                f"{remote_ref}."
            )
        else:
            message = "Update available."

        detail = (
            f"Current {_short_commit(current_commit)}; "
            f"latest {_short_commit(upstream_commit)}."
        )
        if dirty:
            detail = f"{detail} Dirty files: {', '.join(changed_files)}."
        steps.append(
            UpdateStep(
                name="compare",
                status="completed" if can_update or not update_available else "skipped",
                detail=detail,
                command=ancestor_result.command,
            )
        )

        return UpdateResult(
            deployment_mode=deployment_mode,
            supported=True,
            dry_run=True,
            changed=False,
            message=message,
            steps=steps,
            update_available=update_available,
            can_update=can_update,
            current_branch=current_branch,
            current_commit=_short_commit(current_commit),
            upstream_commit=_short_commit(upstream_commit),
        )

    def _cwd(self) -> Path:
        if self.settings.install_dir.exists():
            return self.settings.install_dir
        return Path.cwd()

    def _git_value(self, command: list[str]) -> str | None:
        result = self.runner(command, self._cwd())
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def _update_commands(self) -> list[list[str]]:
        target = f"{self.settings.update_remote}/{self.settings.update_branch}"
        return [
            ["git", "fetch", self.settings.update_remote],
            ["git", "merge", "--ff-only", target],
            [sys.executable, "-m", "pip", "install", "-e", "."],
            [sys.executable, "-m", "alembic", "upgrade", "head"],
        ]


def run_command(command: list[str], cwd: Path | None = None) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return CommandResult(
            command=command,
            returncode=127,
            stderr=str(exc),
        )
    return CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def detect_deployment_mode(settings: Settings) -> str:
    mode = settings.deployment_mode.strip().lower()
    if mode != "auto":
        return mode
    if Path("/.dockerenv").exists():
        return "docker"
    return "native"


def update_support(
    deployment_mode: str,
    *,
    web_updates_enabled: bool,
) -> tuple[bool, str]:
    if deployment_mode == "docker":
        return (
            False,
            "Docker deployments update through Docker Compose, not in-app mutation.",
        )
    if not web_updates_enabled:
        return (
            True,
            "CLI updates are available. Real web updates require AI_QUOTA_MONITOR_ENABLE_WEB_UPDATES=true.",
        )
    return True, "Native and Proxmox updates can run from the CLI or dashboard."


def resolve_backup_dir(settings: Settings) -> Path:
    if settings.backup_dir.is_absolute():
        return settings.backup_dir
    return settings.data_dir / settings.backup_dir


def update_result_to_dict(result: UpdateResult) -> dict[str, object]:
    return {
        "deployment_mode": result.deployment_mode,
        "supported": result.supported,
        "dry_run": result.dry_run,
        "changed": result.changed,
        "message": result.message,
        "update_available": result.update_available,
        "can_update": result.can_update,
        "current_branch": result.current_branch,
        "current_commit": result.current_commit,
        "upstream_commit": result.upstream_commit,
        "steps": [
            {
                "name": step.name,
                "status": step.status,
                "detail": step.detail,
                "command": step.command,
            }
            for step in result.steps
        ],
    }


def system_info_to_dict(info: SystemInfo) -> dict[str, object]:
    return {
        "app_name": info.app_name,
        "deployment_mode": info.deployment_mode,
        "update_supported": info.update_supported,
        "update_message": info.update_message,
        "web_updates_enabled": info.web_updates_enabled,
        "current_branch": info.current_branch,
        "current_commit": info.current_commit,
        "upstream_commit": info.upstream_commit,
        "dirty": info.dirty,
        "install_dir": info.install_dir,
        "data_dir": info.data_dir,
        "database_path": info.database_path,
        "backup_dir": info.backup_dir,
    }


def _database_path(database_url: str) -> str | None:
    sqlite_prefix = "sqlite:///"
    if not database_url.startswith(sqlite_prefix):
        return None
    value = database_url.removeprefix(sqlite_prefix)
    if value == ":memory:":
        return None
    return str(Path(value).expanduser())


def _backup_path(settings: Settings) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return resolve_backup_dir(settings) / f"ai-quota-monitor-{timestamp}.sqlite3"


def _short_commit(value: str | None) -> str | None:
    value = (value or "").strip()
    return value[:8] if value else None


def _changed_files(status: str) -> list[str]:
    changed: list[str] = []
    for line in status.splitlines():
        if not line:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        if path:
            changed.append(path)
    return changed


def _command_error(result: CommandResult) -> str:
    return (result.stderr or result.stdout or f"Command exited {result.returncode}").strip()


def _fetch_failure_message(result: CommandResult) -> str:
    detail = _command_error(result)
    lowered = detail.lower()
    permission_markers = (
        "insufficient permission",
        "failed to write object",
        "unpack-objects failed",
        "permission denied",
    )
    if any(marker in lowered for marker in permission_markers):
        return (
            "Could not check for updates because the Git checkout is not writable by "
            "the service user. Run the Proxmox installer with --update from the LXC "
            "root shell, or repair ownership with: chown -R aiquota:aiquota "
            "/opt/ai-quota-monitor /var/lib/ai-quota-monitor"
        )
    return detail


def _command_failure_message(command: list[str]) -> str:
    if _step_name(command) == "restart":
        return f"Update applied, but restart failed while running {' '.join(command)}."
    return f"Update failed while running {' '.join(command)}."


def _ownership_repair_command(
    settings: Settings,
    deployment_mode: str,
) -> list[str] | None:
    if deployment_mode != "proxmox":
        return None
    if not _is_root():
        return None
    chown = shutil.which("chown")
    if not chown:
        return None
    return [
        chown,
        "-R",
        PROXMOX_SERVICE_OWNER,
        str(settings.install_dir),
        str(settings.data_dir),
    ]


def _restart_unavailable_message(settings: Settings) -> str:
    helper = RESTART_HELPER_DIR / f"{settings.update_service_name}-restart"
    return (
        "Automatic restart is not available for this web update because the "
        f"sudo restart helper was not found at {helper}. Run the Proxmox "
        "installer with --update, or restart manually with: systemctl restart "
        f"{settings.update_service_name}"
    )


def _restart_command(settings: Settings, deployment_mode: str) -> list[str] | None:
    helper = RESTART_HELPER_DIR / f"{settings.update_service_name}-restart"
    sudo = shutil.which("sudo")
    if deployment_mode in {"native", "proxmox"} and _is_root():
        return ["systemctl", "restart", settings.update_service_name]
    if (
        deployment_mode in {"native", "proxmox"}
        and not _is_root()
        and sudo
        and helper.exists()
    ):
        return [sudo, "-n", str(helper)]
    if deployment_mode in {"native", "proxmox"}:
        return None
    return ["systemctl", "restart", settings.update_service_name]


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return callable(geteuid) and geteuid() == 0


def _step_name(command: list[str]) -> str:
    if command[:2] == ["git", "fetch"]:
        return "fetch"
    if command[:2] == ["git", "merge"]:
        return "merge"
    if "pip" in command:
        return "install"
    if "alembic" in command:
        return "migrate"
    if len(command) >= 2 and Path(command[0]).name == "chown" and command[1] == "-R":
        return "ownership"
    if command[:2] == ["systemctl", "restart"] or (
        len(command) >= 2 and Path(command[0]).name == "sudo" and command[1] == "-n"
    ):
        return "restart"
    return command[0]
