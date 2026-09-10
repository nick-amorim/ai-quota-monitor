# Technical Reference

## Stack

- Python 3.10 or newer.
- FastAPI and Uvicorn.
- Jinja2 server-rendered templates.
- Local HTMX-compatible JavaScript adapter for partial refreshes.
- SQLite.
- SQLAlchemy 2.x and Alembic.
- APScheduler.
- Pydantic Settings.
- `openai-codex` Python package.
- pytest and httpx for tests.

The application intentionally avoids React, Angular, a Node build toolchain, PostgreSQL, Redis, Celery, Kubernetes, and ChatGPT web scraping.

## Package Layout

```text
src/ai_quota_monitor/
|-- __main__.py
|-- config.py
|-- database.py
|-- main.py
|-- migrations.py
|-- routes/
|-- services/
|-- models/
|-- static/
`-- templates/
```

## Database Tables

| Table | Purpose |
| --- | --- |
| `accounts` | Account names, slugs, auth status, metadata |
| `account_schedules` | Daily and weekly anchor schedule settings |
| `anchor_runs` | Manual and scheduled anchor history |
| `usage_raw` | Raw app-server telemetry payloads |
| `usage_snapshots` | Normalized quota windows and reset timestamps |
| `events` | Scheduler, telemetry, update, and anchor events |
| `app_settings` | Global app settings such as the anchor prompt |

## Codex Isolation

Each account uses separate runtime paths:

```text
/var/lib/ai-quota-monitor/account-a/codex-home/
/var/lib/ai-quota-monitor/account-a/workspace/
/var/lib/ai-quota-monitor/account-b/codex-home/
/var/lib/ai-quota-monitor/account-b/workspace/
```

The app clears `CODEX_API_KEY` and `OPENAI_API_KEY` in spawned Codex processes. ChatGPT account authentication is handled by the Codex runtime's device-code flow.

## Authentication Backend

The production auth backend uses `openai_codex` to:

- start ChatGPT device-code login;
- read account metadata without forcing refresh tokens into the database;
- log out an account runtime.

Stored metadata includes:

- auth status;
- account display/email when reported;
- plan type when reported;
- last auth check timestamp.

## Anchor Backend

Manual and scheduled anchors use:

- account-specific `CODEX_HOME`;
- account-specific workspace path;
- ephemeral Codex thread per run;
- read-only sandbox;
- deny-all approval mode.

Anchor history stores prompt, status, timestamps, duration, Codex thread and turn IDs when available, final response, token usage, and errors.

## Telemetry Backend

Quota telemetry uses an account-scoped `codex app-server` process and newline-delimited JSON messages:

- `initialize`;
- `initialized`;
- `account/read` with `{"refreshToken": false}`;
- `account/rateLimits/read`.

Normalized windows are identified by `windowDurationMins`:

| Window | Duration |
| --- | ---: |
| 5-hour | 300 minutes |
| Weekly | 10080 minutes |

Observed reset timestamps from Codex are stored separately from expected reset timestamps derived from local schedules.

If app-server payloads are sparse, missing normalized fields carry forward the previous known value. If neither known window can be found, the raw payload is retained and the snapshot is marked `unsupported`.

The database stores Codex's `usedPercent` values. Dashboard and monitor cards display remaining quota as `100 - usedPercent` so the UI aligns with Codex's `Usage remaining` menu.

Manual refresh failures are recorded as `telemetry.refresh.failed` warning events. If no newer successful snapshot exists, the account card enters a telemetry-error state with the stored failure detail.

## Live Updates

When enabled, the app keeps long-running app-server listeners open for connected accounts. `account/rateLimits/updated` notifications are normalized through the same raw-plus-snapshot path as manual refreshes.

Disable listeners with:

```text
AI_QUOTA_MONITOR_ENABLE_APP_SERVER_NOTIFICATIONS=false
```

## Logging

`ai_quota_monitor.logging_config.configure_logging` attaches a rotating file handler to the `ai_quota_monitor` logger during app creation.

Default path:

```text
data/logs/ai-quota-monitor.log
```

Settings:

| Setting | Purpose |
| --- | --- |
| `AI_QUOTA_MONITOR_LOG_LEVEL` | Log level |
| `AI_QUOTA_MONITOR_LOG_FILE` | Explicit log file path |
| `AI_QUOTA_MONITOR_LOG_MAX_BYTES` | Rotation size |
| `AI_QUOTA_MONITOR_LOG_BACKUP_COUNT` | Number of rotated files |

Route handlers and background services log exceptions before returning user-facing redirects or event rows.

## Scheduler

On startup, APScheduler reads account schedules and creates:

- one same-day 5-hour cadence of daily anchor jobs per enabled account when daily anchors are enabled;
- one weekly target anchor job per enabled account.

Daily cadence jobs are derived from `daily_anchor_time` by repeatedly adding the 300-minute 5-hour window until local midnight. A `05:00` start produces `05:00`, `10:00`, `15:00`, and `20:00`; a `09:00` start produces `09:00`, `14:00`, and `19:00`.

Missed jobs use `AI_QUOTA_MONITOR_MISSED_ANCHOR_POLICY`:

| Policy | Behavior |
| --- | --- |
| `run_if_within_grace` | Run missed jobs only inside the grace window |
| `skip_missed` | Skip jobs missed before the scheduler could run them |

Smart scheduled anchors refresh telemetry before and after the anchor and record whether reset timing changed.

The same APScheduler instance also owns the automatic telemetry poll job, `telemetry:refresh-all`. It refreshes connected, enabled accounts only. The interval defaults to `AI_QUOTA_MONITOR_USAGE_POLL_INTERVAL_MINUTES`, then uses the persisted `usage_poll_interval_minutes` app setting when the scheduler reloads.

## Partial Refreshes

The frontend uses server-rendered partials:

| Partial | Refresh |
| --- | --- |
| `/partials/accounts` | Dashboard account cards |
| `/partials/accounts/{account_id}/usage` | Account usage panel |
| `/partials/scheduler` | Scheduled jobs |
| `/partials/events/recent` | Recent events |
| `/partials/monitor` | Compact monitor |
| `/partials/monitor/{account_slug}` | Single-account compact monitor |

`src/ai_quota_monitor/static/app.js` implements the subset of `hx-get`, `hx-trigger`, and `hx-swap` needed by these fragments.

Partial refreshes are browser-only UI refreshes. They use the persisted `dashboard_refresh_interval_seconds` app setting, which defaults to `AI_QUOTA_MONITOR_DASHBOARD_REFRESH_INTERVAL_SECONDS`. This timer does not fetch fresh Codex quota telemetry; the scheduler telemetry job owns that.

## UI Notes

The dashboard is a dark-first operational interface with an optional persisted light mode. The top gear opens global application settings only: deployment/update controls, dashboard refresh seconds, usage polling minutes, and the global anchor prompt. Each account card's `Settings` button opens that account's schedule controls.

Visible account labels use the Codex account email when available. Until an account is authenticated, the UI uses `Account not logged in` instead of internal seed labels such as Account A or Account B.

Account cards show compact schedule context: all enabled daily weekdays, derived daily wake times, the weekly target, and the next scheduled wake call from APScheduler.

All frontend timestamps are formatted in the configured application timezone. SQLite may return UTC datetimes without timezone metadata, so display formatters treat naive database values as UTC before converting them to the local display timezone.

Dashboard quota windows intentionally use compact labels:

- 5-hour: remaining percent, `Reset HH:MM`, and `Anchor HH:MM`
- weekly: remaining percent, `Reset Day HH:MM`, and `Anchor Weekday HH:MM`

The monitor view is dark-only and intentionally dense. It hides the normal app bar, omits the timeline, and uses compact account labels, status dots, reset chips, and quota bars for a 3.7-inch Raspberry Pi display.

## Native Update Helpers

Native/Proxmox installs keep `/opt/ai-quota-monitor` and `/var/lib/ai-quota-monitor` owned by the unprivileged `aiquota` service user. The installer adds `/opt/ai-quota-monitor` to Git's system `safe.directory` list so root-run reinstall/update commands can inspect the repository after ownership has been transferred to `aiquota`.

The administrative updater entry point is a root-owned wrapper at `/usr/local/bin/ai-quota-monitor-update`, with a `/usr/bin` symlink for minimal `pct enter` PATH environments. The wrapper sources `/etc/ai-quota-monitor.env` and then executes the virtualenv updater, so CLI updates use production database and data paths.

Dashboard-triggered real updates run as `aiquota`. Because that user should not receive broad systemd privileges, the installer creates a root-owned `/usr/local/sbin/ai-quota-monitor-restart` helper and a narrow sudoers rule allowing only that helper. `SystemService` uses `sudo -n` for this helper when a non-root Proxmox/native web update requests `--restart`; root CLI updates fall back to direct `systemctl restart`.

The dashboard uses `POST /api/system/update/check` for update availability checks. The check fetches upstream metadata, compares the current checkout with the configured remote branch, ignores untracked files, and reports whether an in-app install can safely fast-forward. `POST /api/system/update` remains the mutating path for dry runs and real installs.
