# ai-quota-monitor

ai-quota-monitor is a self-hosted monitor and scheduler for two independently authenticated ChatGPT Plus/Codex accounts.

The project goal is to make Codex usage windows visible and predictable without using OpenAI API keys, scraping ChatGPT, or storing account credentials in the application database.

## Current Status

Phase 10 deployment and update support is implemented.

Local planning drafts may exist under `docs/`, but that directory is intentionally ignored and not tracked in Git.

The current application provides:

- installable Python package;
- FastAPI app factory;
- SQLite connection initialization;
- `/health` endpoint;
- dashboard at `/`;
- Alembic scaffold;
- account, schedule, and app settings tables;
- Account A and Account B default seed data;
- editable account schedule forms;
- Codex SDK dependency and per-account auth status controls;
- manual Codex anchor execution with persisted run history;
- Codex app-server telemetry refresh with raw and normalized usage persistence;
- APScheduler-backed daily and weekly anchor jobs;
- schedule reload after dashboard schedule changes;
- scheduler event logging;
- smart scheduled-anchor validation with pre/post telemetry checks;
- observed and expected reset timestamps displayed separately;
- app-server rate-limit update notification ingestion;
- refreshable dashboard sections for usage, scheduler status, and recent events;
- filterable event history at `/history`;
- current-day timeline for anchors and reset events;
- compact always-on monitor view at `/monitor`;
- single-account compact monitor views at `/monitor/{account_slug}`;
- dark-first responsive dashboard shell with persisted light/dark toggle;
- dashboard settings drawer for schedules, anchor prompt, and update controls;
- reset display that separates configured, expected, and observed timing;
- weekly reset drift indicators;
- empty, loading, error, and stale telemetry states;
- production Dockerfile and Docker Compose configuration;
- Proxmox/native systemd deployment assets;
- CLI and API update helper with SQLite backup before migrations;
- dashboard system update panel with Docker-specific guidance;
- startup migrations before default seeding;
- pytest smoke tests.

## Remaining Planned Features

- Real-device Proxmox LXC smoke testing.
- Opt-in integration tests against real Codex authentication and telemetry.

## Codex Authentication

Phase 3 adds the Codex SDK dependency and account-scoped authentication controls.

The production auth backend uses the public `openai_codex` Python package and launches Codex with:

- account-specific `CODEX_HOME`;
- account-specific workspace path;
- cleared `CODEX_API_KEY` and `OPENAI_API_KEY` values in the spawned Codex process;
- `login_chatgpt_device_code()` for ChatGPT device-code login;
- `account(refresh_token=False)` for status checks;
- `logout()` for session removal.

The app stores only account metadata in SQLite:

- auth status;
- account display/email when reported by Codex;
- plan type when reported by Codex;
- last auth check timestamp.

It must not store ChatGPT passwords, browser cookies, OAuth access tokens, OAuth refresh tokens, or OpenAI API keys.

If both configured accounts report the same Codex account identity, the later refreshed account is marked `duplicate_account`.

## Anchor Execution

Phase 4 adds manual anchor execution for a connected account.

The production anchor backend uses the same account-scoped Codex SDK isolation as authentication:

- account-specific `CODEX_HOME`;
- account-specific workspace path;
- cleared `CODEX_API_KEY` and `OPENAI_API_KEY` values in the spawned Codex process;
- an ephemeral Codex thread per anchor run;
- `Sandbox.read_only`;
- `ApprovalMode.deny_all`.

The default global anchor prompt is:

```text
Reply only with OK.
```

The prompt is stored in `app_settings` and can be edited from the dashboard.

Each manual anchor run writes an `anchor_runs` record with:

- account;
- prompt;
- status;
- started and completed timestamps;
- duration;
- Codex thread and turn IDs when available;
- final response when available;
- token usage when reported by the SDK;
- error message on failure.

Normal automated tests use fake Codex backends and never run a real anchor turn.

## Usage Telemetry

Phase 5 adds a Codex app-server telemetry path for quota data.

The production telemetry backend launches one account-scoped `codex app-server` process per refresh request and initializes the newline-delimited JSON protocol with:

- `initialize`;
- `initialized`;
- `account/read` with `{"refreshToken": false}`;
- `account/rateLimits/read`.

Each telemetry refresh stores the raw `account/rateLimits/read` response in `usage_raw` before writing a normalized `usage_snapshots` row.

Normalization identifies windows by `windowDurationMins`:

- `300` minutes for the 5-hour window;
- `10080` minutes for the weekly window.

If a later response is partial or sparse, missing normalized fields carry forward the previous known-good value for that account. If the app-server response shape changes and neither expected window can be found, the raw payload is still retained and the snapshot is marked `unsupported` rather than showing invented quota data.

Usage snapshots store observed reset timestamps returned by Codex separately from expected reset timestamps derived from the configured account schedule. The dashboard displays both values for the 5-hour and weekly windows.

Phase 8 keeps an optional long-running app-server listener open for each enabled, connected account. The listener handles `account/rateLimits/updated` notifications and writes them through the same raw-plus-normalized telemetry path as manual refreshes. Sparse notification payloads merge into the previous snapshot so missing windows do not erase known 5-hour or weekly values.

Notification listeners are enabled by default and can be disabled with:

```text
AI_QUOTA_MONITOR_ENABLE_APP_SERVER_NOTIFICATIONS=false
```

## Scheduler and Smart Anchors

Phase 6 adds background anchor scheduling through APScheduler.

On application startup, the scheduler reads account schedules from SQLite and creates:

- one daily anchor job per enabled account when daily anchors are enabled;
- one weekly target anchor job per enabled account.

Daily jobs honor each account's weekday toggles, daily anchor time, and timezone. Weekly jobs honor each account's weekly target day, weekly target time, and timezone.

When a schedule is saved from the dashboard, jobs are reloaded immediately without restarting the application. The dashboard shows scheduler status, active scheduled jobs, and the next run timestamp reported by APScheduler.

Missed jobs follow the configured missed-anchor policy:

- `run_if_within_grace` runs a missed job only if it is inside the configured grace window;
- `skip_missed` skips jobs that were missed before the scheduler could run them.

Phase 7 routes scheduled jobs through smart validation before spending an anchor turn.

Scheduled anchor validation:

1. refreshes quota telemetry before the anchor;
2. skips the anchor when the observed 5-hour window is already active and `skip_if_window_active` is enabled;
3. sends the anchor when telemetry says no active window is present, or when pre-anchor telemetry is unavailable;
4. refreshes telemetry after the anchor;
5. records whether the observed reset timestamp moved as expected.

Manual anchors still run immediately from the dashboard button.

The scheduler uses the same account-scoped locking path as manual anchors, so overlapping anchor runs for the same account are blocked. Scheduler starts, reloads, missed jobs, failures, skipped jobs, completed scheduled anchors, and smart-anchor verification outcomes are recorded in `events`.

## Events and Live Updates

The dashboard uses server-rendered partials for sections that can change while the app is open:

- quota telemetry refreshes every 30 seconds per account;
- scheduled jobs refresh every 30 seconds;
- recent events refresh every 15 seconds.

The app ships a small local HTMX-compatible adapter for the `hx-get`, `hx-trigger`, and `hx-swap` attributes used by these fragments, so no Node build toolchain or external browser dependency is required.

The full event history is available at:

```text
http://127.0.0.1:8080/history
```

History can be filtered by account, severity level, and category prefix. Telemetry notification lifecycle events, scheduler events, smart-anchor decisions, and anchor failures are persisted there for debugging without mixing operational noise into the main account cards.

## Dashboard Interface

The dashboard defaults to a dark operational theme and stores the user's light/dark preference in browser local storage. The shell keeps account state, usage windows, scheduler health, recent events, and the current-day timeline visible on the main page.

Configuration-heavy controls live in the Settings drawer:

- account schedule targets;
- global anchor prompt;
- system update dry-run and execution controls.

This keeps the dashboard focused for daily monitoring while still making operational controls available without leaving the page.

## Timeline and Compact Monitor

The management dashboard includes a current-day timeline for:

- scheduled daily and weekly anchors;
- expected 5-hour and weekly reset times;
- observed 5-hour and weekly reset times from the latest Codex telemetry.

The compact monitor is available at:

```text
http://127.0.0.1:8080/monitor
```

Single-account monitor views are available by slug:

```text
http://127.0.0.1:8080/monitor/account-a
http://127.0.0.1:8080/monitor/account-b
```

The compact monitor always uses the dark monitor theme, even if the dashboard is switched to light mode. It avoids tables and fixed-width content so it can run on small always-on displays such as a Raspberry Pi screen. It refreshes itself every 15 seconds through server-rendered partials.

Each quota window shows:

- current usage percentage when telemetry exists;
- configured schedule target;
- expected reset time;
- observed reset time;
- status for waiting, active, stale, warning, high, and unsupported telemetry states.

The weekly window also shows a drift indicator comparing observed reset timing to the expected schedule.

## Deployment and Updates

Phase 10 adds two supported deployment paths: Docker Compose and native/Proxmox systemd.

### Update Model

Native and Proxmox installs update from the Git checkout in `/opt/ai-quota-monitor`. Before migrations run, the updater copies the SQLite database into the configured backup directory. Runtime state remains under `/var/lib/ai-quota-monitor`, including SQLite data, account-scoped Codex homes, workspaces, raw telemetry, history, and auth state.

Docker deployments are immutable from inside the container. The app reports update status, but real Docker updates should be done by rebuilding or pulling the image and recreating the Compose service.

Available update commands after installation:

```bash
ai-quota-monitor-update --dry-run
ai-quota-monitor-update --yes --restart
quotapilot-update --yes --restart
update --yes --restart
```

The generic `update` script is provided because the deployment story requested it. `ai-quota-monitor-update` is the preferred explicit command name.

Updater options:

| Option | Purpose |
| --- | --- |
| `--dry-run` | Show backup and update steps without changing the checkout. |
| `--yes` | Run non-interactively. |
| `--advanced` | Print command details for each step. |
| `--restart` | Restart the configured systemd service after migrations. |
| `--install-dir PATH` | Override the Git checkout directory. |
| `--data-dir PATH` | Override runtime data directory. |
| `--backup-dir PATH` | Override backup directory. |
| `--deployment-mode MODE` | Force `native`, `proxmox`, `docker`, or `auto`. |

System API routes:

| Route | Purpose |
| --- | --- |
| `GET /api/system/info` | Return deployment mode, Git revision, update support, data path, database path, and backup path. |
| `POST /api/system/update` | Run a dry-run update plan by default, or a real update when `dry_run=false` and web updates are enabled. |

Real web updates are disabled by default for native development. Proxmox installs created by the provided script set:

```text
AI_QUOTA_MONITOR_ENABLE_WEB_UPDATES=true
```

The dashboard System panel uses the same updater service as the CLI and API.

## Planned Stack

- Python 3.10 or newer.
- FastAPI and Uvicorn.
- Jinja2 and HTMX-style server-rendered partials.
- SQLite.
- SQLAlchemy 2.x and Alembic.
- APScheduler.
- Pydantic Settings.
- Official Codex authentication/runtime mechanisms.
- pytest and httpx for tests.

V1 should not use React, Angular, a Node build toolchain, PostgreSQL, Redis, Celery, Kubernetes, or ChatGPT web scraping.

## Architecture

ai-quota-monitor uses two Codex integration paths:

- a Python SDK/runtime path for account login and anchor turns;
- an app-server telemetry path for account and quota data, if verified as available in the installed official runtime.

Each account must have a separate Codex home and workspace:

```text
/var/lib/ai-quota-monitor/account-a/codex-home/
/var/lib/ai-quota-monitor/account-a/workspace/
/var/lib/ai-quota-monitor/account-b/codex-home/
/var/lib/ai-quota-monitor/account-b/workspace/
```

The application database stores configuration, normalized telemetry, raw telemetry payloads, anchor runs, and events. It must not store OAuth tokens or account credentials.

Currently implemented tables:

- `accounts`
- `account_schedules`
- `anchor_runs`
- `usage_raw`
- `usage_snapshots`
- `events`
- `app_settings`

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

Implemented schedule fields:

- account enabled;
- daily anchor enabled;
- daily anchor time;
- weekly target day;
- weekly target time;
- account timezone;
- active weekdays;
- skip anchor when a 5-hour window is already active.

Implemented scheduler settings:

- scheduler enabled flag through `AI_QUOTA_MONITOR_ENABLE_SCHEDULER`;
- missed-anchor policy through `AI_QUOTA_MONITOR_MISSED_ANCHOR_POLICY`;
- missed-anchor grace window through `AI_QUOTA_MONITOR_MISSED_ANCHOR_GRACE_MINUTES`.

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

## Project Structure

```text
ai-quota-monitor/
|-- alembic/
|-- src/
|   `-- ai_quota_monitor/
|       |-- __main__.py
|       |-- config.py
|       |-- database.py
|       |-- main.py
|       |-- models/
|       |-- routes/
|       |-- services/
|       |-- static/
|       `-- templates/
|-- tests/
|-- pyproject.toml
|-- alembic.ini
|-- README.md
`-- SECURITY.md
```

## Local Development

Create a virtual environment and install the project:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Start the application:

```powershell
.\.venv\Scripts\python.exe -m ai_quota_monitor
```

The application listens on:

```text
http://127.0.0.1:8080
```

Available routes:

| Route | Purpose |
| --- | --- |
| `/` | Account and schedule dashboard |
| `/monitor` | Compact all-account monitor |
| `/monitor/{account_slug}` | Compact single-account monitor |
| `/history` | Filterable event history |
| `/health` | JSON health check with database status |
| `/api/system/info` | JSON deployment and update status |
| `POST /api/system/update` | Dry-run or real native/Proxmox update |
| `/partials/monitor` | Refreshable compact monitor partial |
| `/partials/monitor/{account_slug}` | Refreshable single-account monitor partial |
| `/partials/accounts/{account_id}/usage` | Refreshable account usage partial |
| `/partials/scheduler` | Refreshable scheduled jobs partial |
| `/partials/events/recent` | Refreshable recent events partial |
| `POST /accounts/{account_id}/schedule` | Persist account schedule changes |
| `POST /accounts/{account_id}/auth/device-login` | Start ChatGPT device-code login |
| `POST /accounts/{account_id}/auth/cancel` | Cancel a pending device-code login |
| `POST /accounts/{account_id}/auth/status` | Refresh Codex account status |
| `POST /accounts/{account_id}/auth/logout` | Clear the account's Codex session |
| `POST /settings/anchor` | Update the global anchor prompt |
| `POST /accounts/{account_id}/anchors/run` | Run one manual anchor for a connected account |
| `POST /accounts/{account_id}/usage/refresh` | Refresh quota telemetry for one account |
| `POST /usage/refresh-all` | Refresh quota telemetry for all accounts |

Runtime data should live in `data/` locally and must not be committed.

Run Alembic migrations against a clean target database:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

## Deployment

### Proxmox LXC

Create a new Proxmox LXC from a Proxmox host:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/nick-amorim/ai-quota-monitor/main/scripts/proxmox/install-lxc.sh)
```

Non-interactive Proxmox creation requires a container ID:

```bash
AI_QUOTA_MONITOR_CT_ID=120 bash <(curl -fsSL https://raw.githubusercontent.com/nick-amorim/ai-quota-monitor/main/scripts/proxmox/install-lxc.sh) --yes
```

Advanced mode prompts for container ID, storage, disk, memory, swap, CPU, and network bridge:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/nick-amorim/ai-quota-monitor/main/scripts/proxmox/install-lxc.sh) --advanced
```

Install into an existing Debian/Ubuntu LXC or VM:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/nick-amorim/ai-quota-monitor/main/scripts/proxmox/install-lxc.sh) --existing
```

Update an existing native/Proxmox install:

```bash
ai-quota-monitor-update --yes --restart
```

Default Proxmox container settings:

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
| Hostname | ai-quota-monitor |

Native/Proxmox runtime layout:

```text
/opt/ai-quota-monitor/        application checkout and virtualenv
/var/lib/ai-quota-monitor/    database, Codex homes, runtime state
/etc/ai-quota-monitor.env     deployment configuration
/etc/systemd/system/          systemd service
```

Updates must preserve `/var/lib/ai-quota-monitor`.

### Docker Compose

Start the app with Docker Compose:

```bash
docker compose up -d
```

Persistent data will live in a volume mounted at:

```text
/var/lib/ai-quota-monitor
```

Docker deployments should update by pulling a newer image and restarting the container, not by mutating the running container from inside the web app.

Typical local image update:

```bash
docker compose build --pull
docker compose up -d
```

## Documentation Policy

The README is the tracked source of truth until a public documentation structure is introduced.

The `docs/` folder is ignored so local drafts, AI-agent notes, and planning artifacts can exist without entering repository history.

At the end of each implementation phase, update this README with any user-facing setup, deployment, feature, or operational details that changed.
