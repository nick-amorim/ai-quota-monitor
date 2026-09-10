# Deployment Guide

## Configuration

Copy values from `.env.example` into your deployment environment.

Important settings:

| Variable | Purpose |
| --- | --- |
| `AI_QUOTA_MONITOR_ENV` | Environment label |
| `AI_QUOTA_MONITOR_HOST` | Bind host |
| `AI_QUOTA_MONITOR_PORT` | Bind port |
| `AI_QUOTA_MONITOR_TIMEZONE` | Default display and schedule timezone |
| `AI_QUOTA_MONITOR_DATABASE_URL` | SQLite database URL |
| `AI_QUOTA_MONITOR_DATA_DIR` | Runtime data directory |
| `AI_QUOTA_MONITOR_LOG_LEVEL` | Python app log level |
| `AI_QUOTA_MONITOR_LOG_FILE` | Rotating app log path, defaults under `data/logs` |
| `AI_QUOTA_MONITOR_LOG_MAX_BYTES` | Maximum size before log rotation |
| `AI_QUOTA_MONITOR_LOG_BACKUP_COUNT` | Number of rotated log files to keep |
| `AI_QUOTA_MONITOR_DASHBOARD_REFRESH_INTERVAL_SECONDS` | Browser-only dashboard and monitor partial refresh interval |
| `AI_QUOTA_MONITOR_USAGE_POLL_INTERVAL_MINUTES` | Background Codex quota telemetry polling interval |
| `AI_QUOTA_MONITOR_ENABLE_SCHEDULER` | Enable background scheduled anchors |
| `AI_QUOTA_MONITOR_ENABLE_APP_SERVER_NOTIFICATIONS` | Enable live quota update listeners |
| `AI_QUOTA_MONITOR_ENABLE_WEB_UPDATES` | Allow real native/Proxmox web updates |
| `AI_QUOTA_MONITOR_DEPLOYMENT_MODE` | `auto`, `native`, `proxmox`, or `docker` |

## Docker Compose

Start:

```bash
docker compose up -d
```

Build and restart:

```bash
docker compose build --pull
docker compose up -d
```

Docker uses:

```text
AI_QUOTA_MONITOR_HOST=0.0.0.0
AI_QUOTA_MONITOR_PORT=8080
AI_QUOTA_MONITOR_DATABASE_URL=sqlite:////var/lib/ai-quota-monitor/ai-quota-monitor.sqlite3
AI_QUOTA_MONITOR_DATA_DIR=/var/lib/ai-quota-monitor
AI_QUOTA_MONITOR_DEPLOYMENT_MODE=docker
AI_QUOTA_MONITOR_ENABLE_WEB_UPDATES=false
```

Docker deployments are immutable from inside the running container. The dashboard can report update status, but real Docker updates should rebuild or pull the image and recreate the service.

Quota telemetry uses `codex app-server` inside the container. The image validates that the packaged Codex CLI runtime is present during build. If the dashboard reports `Codex CLI is not available`, rebuild and recreate the container:

```bash
docker compose build --pull
docker compose up -d
```

## Proxmox LXC

Create a new LXC from a Proxmox host:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/nick-amorim/ai-quota-monitor/main/scripts/proxmox/install-lxc.sh)
```

Non-interactive creation with a chosen container ID:

```bash
AI_QUOTA_MONITOR_CT_ID=120 bash <(curl -fsSL https://raw.githubusercontent.com/nick-amorim/ai-quota-monitor/main/scripts/proxmox/install-lxc.sh) --yes
```

Advanced mode:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/nick-amorim/ai-quota-monitor/main/scripts/proxmox/install-lxc.sh) --advanced
```

Install into an existing Debian/Ubuntu LXC or VM:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/nick-amorim/ai-quota-monitor/main/scripts/proxmox/install-lxc.sh) --existing
```

Default Proxmox settings:

| Setting | Value |
| --- | --- |
| OS | Debian 13 |
| Type | Unprivileged LXC |
| CPU | 1 core |
| RAM | 1 GB |
| Swap | 512 MB |
| Disk | 8 GB |
| Network | DHCP on `vmbr0` |
| Port | 8080 |
| Hostname | `ai-quota-monitor` |

## Native Layout

```text
/opt/ai-quota-monitor/        application checkout and virtualenv
/var/lib/ai-quota-monitor/    database, Codex homes, workspaces, runtime state
/etc/ai-quota-monitor.env     deployment configuration
/etc/systemd/system/          systemd service
```

Updates must preserve `/var/lib/ai-quota-monitor`.

## Updates

Dashboard flow:

1. Open **Settings**.
2. Click **Check update**.
3. If an update is available and the checkout can fast-forward cleanly, click **Install update**.

Real dashboard installs are disabled unless `AI_QUOTA_MONITOR_ENABLE_WEB_UPDATES=true`. Docker deployments should use Compose updates instead of dashboard installs.
Dashboard and CLI updates ignore untracked Git files during the safety check, so generated caches or local runtime scratch files do not block a safe fast-forward. Tracked local changes still block updates.

Preferred update command:

```bash
ai-quota-monitor-update --yes --restart
```

The installer creates root-owned command wrappers in `/usr/local/bin` and `/usr/bin`, so the command works from normal root shells and minimal `pct enter` environments. If needed, use the explicit fallback:

```bash
/usr/bin/ai-quota-monitor-update --yes --restart
```

The wrapper and Python updater load `/etc/ai-quota-monitor.env` before resolving database, data, backup, install, deployment, or restart settings. Production Proxmox backups therefore use `/var/lib/ai-quota-monitor`, not the development fallback under `./data`.
When the updater runs as root in Proxmox mode, it repairs `/opt/ai-quota-monitor` and `/var/lib/ai-quota-monitor` ownership back to `aiquota:aiquota` before restarting. This prevents root-created Git objects from breaking later dashboard update checks.

Dry run:

```bash
ai-quota-monitor-update --dry-run
```

Updater options:

| Option | Purpose |
| --- | --- |
| `--dry-run` | Show backup and update steps without changing the checkout |
| `--yes` | Run non-interactively |
| `--advanced` | Print command details for each step |
| `--restart` | Restart the configured systemd service after migrations |
| `--install-dir PATH` | Override the Git checkout directory |
| `--data-dir PATH` | Override runtime data directory |
| `--backup-dir PATH` | Override backup directory |
| `--deployment-mode MODE` | Force `native`, `proxmox`, `docker`, or `auto` |

Aliases:

```bash
quotapilot-update --yes --restart
```

`ai-quota-monitor-update` is the preferred explicit command name. The generic `update` Python entry point exists only inside the virtual environment and should not be used as the documented administrative command.

For older existing LXCs, rerun the installer once as root to refresh the command wrappers, Git safe-directory configuration, and web restart helper:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/nick-amorim/ai-quota-monitor/main/scripts/proxmox/install-lxc.sh) --existing
```

The installer's `--update` mode performs the same local wrapper/helper refresh before delegating to the updater. It also ensures `sudo` and `/etc/sudoers.d` exist before installing the dashboard restart helper, then bootstraps the Git checkout with a tracked-only fast-forward so older updater versions can recover from untracked install artifacts:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/nick-amorim/ai-quota-monitor/main/scripts/proxmox/install-lxc.sh) --update
```

Use the same `--update` command to recover an LXC where **Check update** reports a Git object permission error. It repairs ownership before delegating to the updater and again after the update completes.

If a dashboard-triggered update applies Git, pip, and migration steps but reports that restart was skipped, restart manually from the LXC root shell:

```bash
systemctl restart ai-quota-monitor
```
