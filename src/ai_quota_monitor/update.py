from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from ai_quota_monitor.config import Settings, get_settings
from ai_quota_monitor.services.system import SystemService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update an ai-quota-monitor deployment.")
    parser.add_argument("--dry-run", action="store_true", help="Show the update plan only.")
    parser.add_argument("--yes", action="store_true", help="Run without an interactive prompt.")
    parser.add_argument("--advanced", action="store_true", help="Show detailed step output.")
    parser.add_argument("--restart", action="store_true", help="Restart the systemd service after updating.")
    parser.add_argument("--install-dir", type=Path, help="Application checkout directory.")
    parser.add_argument("--data-dir", type=Path, help="Runtime data directory.")
    parser.add_argument("--backup-dir", type=Path, help="Database backup directory.")
    parser.add_argument("--deployment-mode", help="Override deployment mode: native, proxmox, docker, or auto.")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/etc/ai-quota-monitor.env"),
        help="Environment file to load before resolving deployment settings.",
    )
    args = parser.parse_args(argv)

    _load_env_file(args.env_file)
    settings = _settings_from_args(args)
    if not args.dry_run and not args.yes:
        answer = input("Type update to run the ai-quota-monitor updater: ").strip()
        if answer != "update":
            print("Update cancelled.")
            return 1

    result = SystemService(settings).update(dry_run=args.dry_run, restart=args.restart)
    print(result.message)
    for step in result.steps:
        print(f"- {step.name}: {step.status} - {step.detail}")
        if args.advanced and step.command:
            print(f"  command: {' '.join(step.command)}")

    return 0 if result.supported and not any(step.status == "failed" for step in result.steps) else 1


def _settings_from_args(args: argparse.Namespace) -> Settings:
    settings = get_settings()
    overrides = settings.model_dump()
    for key in ("install_dir", "data_dir", "backup_dir", "deployment_mode"):
        value = getattr(args, key)
        if value is not None:
            overrides[key] = value
    return Settings(**overrides)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    load_dotenv(path, override=False)
    if str(path) != ".env":
        os.environ.setdefault("AI_QUOTA_MONITOR_ENV_FILE", str(path))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
