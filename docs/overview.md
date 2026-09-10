# Overview

ai-quota-monitor is a self-hosted FastAPI application for monitoring multiple ChatGPT Plus/Codex accounts. It keeps account authentication isolated, reads Codex quota telemetry, schedules anchor turns, and exposes both a full dashboard and a compact Raspberry Pi monitor.

## Goals

- Make 5-hour and weekly Codex usage windows visible.
- Keep ChatGPT Plus/Codex accounts independently authenticated.
- Avoid OpenAI API keys for quota tracking.
- Avoid ChatGPT web scraping.
- Avoid storing passwords, browser cookies, OAuth access tokens, OAuth refresh tokens, or account credentials in SQLite.
- Provide a simple Docker and Proxmox/native deployment path.

## Implemented Features

- Installable Python package named `ai-quota-monitor`.
- FastAPI application factory and Uvicorn entry point.
- SQLite persistence through SQLAlchemy and Alembic.
- Two initial seed records for compatibility, with dynamic account creation from the dashboard.
- Per-account ChatGPT device-code login controls through the Codex runtime.
- Manual anchor turns with persisted run history.
- Codex app-server telemetry refresh with raw and normalized storage.
- Live app-server quota update listener support.
- APScheduler-backed daily and weekly scheduled anchors.
- Smart scheduled-anchor validation before spending an anchor turn.
- Dashboard at `/`.
- Compact monitor at `/monitor` and `/monitor/{account_slug}`.
- Event history at `/history`.
- Health check at `/health`.
- Native/Proxmox update helper with database backup before migrations.
- Docker Compose deployment.

## User Interface

The dashboard is dark-first and operational. The main screen keeps account state, usage windows, scheduler health, recent events, and the current-day timeline visible. Configuration-heavy controls live in the Settings drawer:

- account creation and account schedule targets;
- global anchor prompt;
- system update dry-run and execution controls.

The compact monitor is designed for a 3.7-inch Raspberry Pi display. It removes dashboard navigation and timeline details, keeps the dark theme, and focuses on account identity, quota percentages, status dots, reset time chips, and progress bars.

## Core Routes

| Route | Purpose |
| --- | --- |
| `/` | Main dashboard |
| `/monitor` | Compact all-account monitor |
| `/monitor/{account_slug}` | Compact single-account monitor |
| `/history` | Filterable event history |
| `/health` | JSON health check |
| `/api/system/info` | Deployment and update status |
| `POST /api/system/update/check` | Fetch and compare local checkout against upstream |
| `POST /api/system/update` | Dry-run or real native/Proxmox update |
| `POST /accounts` | Create a new isolated Codex account profile |
| `POST /accounts/{account_id}/archive` | Archive an account while preserving history and runtime files |

## Runtime State

Local development defaults to `data/`. Native/Proxmox deployments use `/var/lib/ai-quota-monitor`.

Runtime state includes:

- SQLite database;
- account-scoped Codex homes;
- account-scoped workspaces;
- raw telemetry;
- normalized usage snapshots;
- anchor history;
- events;
- backups.
