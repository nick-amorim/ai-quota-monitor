# ai-quota-monitor

ai-quota-monitor is a self-hosted dashboard, scheduler, and small-screen monitor for two independently authenticated ChatGPT Plus/Codex accounts.

It helps make Codex quota windows visible and predictable without OpenAI API keys, ChatGPT web scraping, or storing account credentials in the application database.

## What It Does

- Tracks two account profiles, schedules, authentication state, and quota telemetry.
- Shows a dark-first dashboard at `/` for account status, usage, events, schedules, and updates.
- Provides a compact Raspberry Pi monitor at `/monitor` and `/monitor/{account_slug}`.
- Refreshes connected-account usage automatically on the configured polling interval.
- Runs manual anchors, weekly anchors, and derived same-day 5-hour wake anchors.
- Stores raw telemetry, normalized usage snapshots, anchor history, and operational events in SQLite.
- Supports Docker Compose and native/Proxmox systemd deployments.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m ai_quota_monitor
```

Open:

```text
http://127.0.0.1:8080
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Docker

From the machine that will run Docker:

```bash
git clone https://github.com/nick-amorim/ai-quota-monitor.git
cd ai-quota-monitor
docker compose up -d
```

Open the dashboard from the Docker host:

```text
http://localhost:8080
```

Open it from another device on the same network:

```text
http://<docker-host-ip>:8080
```

Useful pages:

```text
http://<docker-host-ip>:8080/          dashboard
http://<docker-host-ip>:8080/monitor   compact monitor
http://<docker-host-ip>:8080/history   event history
```

The app does not create a default dashboard username or password. Use the account `Login` buttons to connect each ChatGPT/Codex account with the device-code flow.

Common Docker commands must be run from the cloned `ai-quota-monitor` directory:

```bash
docker compose logs -f
docker compose restart
docker compose down
```

The container listens on port `8080` and stores persistent runtime state in the `ai-quota-monitor-data` volume mounted at `/var/lib/ai-quota-monitor`. Docker images include a build-time Codex CLI runtime check because quota telemetry reads `codex app-server`.

To rebuild and restart a local image:

```bash
docker compose build --pull
docker compose up -d
```

If the dashboard shows a telemetry error such as `Codex CLI is not available`, rebuild and recreate the container with the commands above.

## Proxmox / Native Install

Create a new Proxmox LXC from a Proxmox host:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/nick-amorim/ai-quota-monitor/main/scripts/proxmox/install-lxc.sh)
```

After the installer finishes, open:

```text
http://<container-ip>:8080
```

Find the container IP from the Proxmox UI, or from the Proxmox host:

```bash
pct list
pct exec <ct-id> -- hostname -I
```

Install into an existing Debian/Ubuntu LXC or VM:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/nick-amorim/ai-quota-monitor/main/scripts/proxmox/install-lxc.sh) --existing
```

For an existing LXC or VM, open:

```text
http://<lxc-or-vm-ip>:8080
```

There is no default dashboard login password. The app is intended for trusted local/LAN deployment, and ChatGPT/Codex accounts are connected from the dashboard with the `Login` button.

The Proxmox installer also does not set an LXC root password. Manage the container from the Proxmox host with:

```bash
pct enter <ct-id>
```

If you want password-based root console/login inside the LXC, set one explicitly:

```bash
pct exec <ct-id> -- passwd
```

Update an existing native/Proxmox install:

```bash
ai-quota-monitor-update --yes --restart
```

Runtime layout:

```text
/opt/ai-quota-monitor/        application checkout and virtualenv
/var/lib/ai-quota-monitor/    database, Codex homes, workspaces, telemetry, history
/etc/ai-quota-monitor.env     deployment configuration
/etc/systemd/system/          systemd service
```

## Main Routes

| Route | Purpose |
| --- | --- |
| `/` | Main dashboard |
| `/monitor` | Compact all-account monitor |
| `/monitor/{account_slug}` | Compact single-account monitor |
| `/history` | Filterable event history |
| `/health` | JSON health check |
| `/api/system/info` | Deployment and update status |

## Configuration

Copy `.env.example` values into your deployment environment. Important settings:

| Variable | Purpose |
| --- | --- |
| `AI_QUOTA_MONITOR_HOST` / `AI_QUOTA_MONITOR_PORT` | Bind address and port |
| `AI_QUOTA_MONITOR_DATABASE_URL` | SQLite database URL |
| `AI_QUOTA_MONITOR_DATA_DIR` | Runtime state directory |
| `AI_QUOTA_MONITOR_LOG_FILE` | Rotating app log path |
| `AI_QUOTA_MONITOR_TIMEZONE` | Default display/schedule timezone |
| `AI_QUOTA_MONITOR_ENABLE_SCHEDULER` | Enable daily/weekly anchor jobs |
| `AI_QUOTA_MONITOR_ENABLE_APP_SERVER_NOTIFICATIONS` | Enable live quota update listeners |
| `AI_QUOTA_MONITOR_ENABLE_WEB_UPDATES` | Allow real web-triggered native/Proxmox updates |

The app must not store ChatGPT passwords, browser cookies, OAuth tokens, refresh tokens, or OpenAI API keys.

## Documentation

Detailed documentation lives in `docs/`:

- [Overview](docs/overview.md)
- [Usage Guide](docs/usage.md)
- [Deployment Guide](docs/deployment.md)
- [Technical Reference](docs/technical-reference.md)
- [Development Guide](docs/development.md)

Local planning drafts and agent scratch notes belong in ignored `develop_docs/`.
