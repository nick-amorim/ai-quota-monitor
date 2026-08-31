# QuotaPilot

QuotaPilot is a planned self-hosted monitor and scheduler for two independently authenticated ChatGPT Plus/Codex accounts.

The project goal is to make Codex usage windows visible and predictable without using OpenAI API keys, scraping ChatGPT, or storing account credentials in the application database.

## Current Status

This repository is in preparation. No runnable application has been implemented yet.

Local planning drafts may exist under `docs/`, but that directory is intentionally ignored and not tracked in Git.

## Planned Features

- Isolated Codex authentication for Account A and Account B.
- Current 5-hour quota usage and reset time.
- Current weekly quota usage and reset time.
- Configurable scheduled Codex anchor turns.
- Persistent observed reset timestamps from Codex telemetry.
- Schedule editor in the dashboard.
- Anchor execution history and event logs.
- Compact `/monitor` view for tiny always-on displays.
- Proxmox LXC deployment.
- Docker Compose deployment.
- Safe update flow that preserves SQLite data and Codex authentication homes.

## Planned Stack

- Python 3.10 or newer.
- FastAPI and Uvicorn.
- Jinja2 and HTMX.
- SQLite.
- SQLAlchemy 2.x and Alembic.
- APScheduler.
- Pydantic Settings.
- Official Codex authentication/runtime mechanisms.
- pytest and httpx for tests.

V1 should not use React, Angular, a Node build toolchain, PostgreSQL, Redis, Celery, Kubernetes, or ChatGPT web scraping.

## Architecture

QuotaPilot will use two Codex integration paths:

- a Python SDK/runtime path for account login and anchor turns;
- an app-server telemetry path for account and quota data, if verified as available in the installed official runtime.

Each account must have a separate Codex home and workspace:

```text
/var/lib/quotapilot/account-a/codex-home/
/var/lib/quotapilot/account-a/workspace/
/var/lib/quotapilot/account-b/codex-home/
/var/lib/quotapilot/account-b/workspace/
```

The application database stores configuration, normalized telemetry, raw telemetry payloads, anchor runs, and events. It must not store OAuth tokens or account credentials.

## Default Schedule

Timezone:

```text
America/Recife
```

Initial defaults:

| Account | Daily anchor | Weekly target |
| --- | ---: | --- |
| A | 05:00 | Monday 05:00 |
| B | 09:00 | Wednesday 09:00 |

These are database defaults only. The dashboard must allow them to be changed.

## Development Workflow

Development is organized by phase branches.

Expected flow:

```text
git switch main
git pull
git switch -c phase/01-skeleton
# implement the phase
# run checks
git commit
git push -u origin phase/01-skeleton
gh pr create --base main --head phase/01-skeleton
```

Each PR should include:

- implementation summary;
- verification results;
- README updates when behavior, setup, deployment, or usage changes;
- known limitations.

Preferred commit shape:

```text
Short imperative summary

- Concrete change
- Verification or documentation note
```

## Local Development

Local development commands will be finalized in Phase 1. The expected shape is:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m quotapilot
```

The application is expected to listen on:

```text
http://127.0.0.1:8080
```

Runtime data should live in `data/` locally and must not be committed.

## Planned Deployment

### Proxmox LXC

The target Proxmox flow is:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/nick-amorim/ai-quota-monitor/main/scripts/proxmox/install-lxc.sh)
```

Planned defaults:

| Setting | Value |
| --- | --- |
| OS | Debian 13 |
| Type | Unprivileged LXC |
| CPU | 1 core |
| RAM | 1 GB |
| Swap | 512 MB |
| Disk | 8 GB |
| Network | DHCP on vmbr0 |
| Port | 8080 |
| Hostname | quotapilot |

Native/Proxmox runtime layout:

```text
/opt/quotapilot/              application checkout and virtualenv
/var/lib/quotapilot/          database, Codex homes, runtime state
/etc/quotapilot.env           deployment configuration
/etc/systemd/system/          systemd service
```

Updates must preserve `/var/lib/quotapilot`.

### Docker Compose

Docker Compose support is planned for Phase 10. The intended shape is:

```bash
docker compose up -d
```

Persistent data will live in a volume mounted at:

```text
/var/lib/quotapilot
```

Docker deployments should update by pulling a newer image and restarting the container, not by mutating the running container from inside the web app.

## Documentation Policy

The README is the tracked source of truth until a public documentation structure is introduced.

The `docs/` folder is ignored so local drafts, AI-agent notes, and planning artifacts can exist without entering repository history.

At the end of each implementation phase, update this README with any user-facing setup, deployment, feature, or operational details that changed.
