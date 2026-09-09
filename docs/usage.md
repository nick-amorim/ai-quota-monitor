# Usage Guide

## Start The App

Local development:

```powershell
.\.venv\Scripts\python.exe -m ai_quota_monitor
```

Docker:

```bash
docker compose up -d
```

Open the dashboard:

```text
http://127.0.0.1:8080
```

## Accounts

The app seeds two accounts:

| Account | Default daily anchor | Default weekly target |
| --- | ---: | --- |
| Account A | 05:00 | Monday 05:00 |
| Account B | 09:00 | Wednesday 09:00 |

Each account has independent Codex runtime state. Account authentication is checked and stored as metadata only. The app does not store account credentials or OAuth tokens in SQLite.

## Authentication Flow

1. Open `/`.
2. Use `Start device login` for one account.
3. Complete the Codex ChatGPT device-code flow.
4. Use `Check status` to refresh account metadata.
5. Repeat for the second account.

If both configured accounts resolve to the same Codex account identity, the later refreshed account is marked as `duplicate_account`.

## Quota Telemetry

Connected accounts are refreshed automatically every `AI_QUOTA_MONITOR_USAGE_POLL_INTERVAL_MINUTES` minutes. Use `Refresh usage` when you want an immediate manual refresh.

The dashboard and compact monitor display remaining quota percentage, matching the Codex usage menu. The app stores Codex's raw `usedPercent` values and derives remaining quota as `100 - usedPercent` for display.

The app stores:

- raw app-server payloads in `usage_raw`;
- normalized 5-hour and weekly values in `usage_snapshots`;
- observed reset timestamps from Codex;
- expected reset timestamps derived from local schedules.

Sparse telemetry responses merge with the previous known snapshot so missing windows do not erase known values.

## Anchor Turns

Manual anchors run immediately from the dashboard for connected accounts.

The default anchor prompt is:

```text
Reply only with OK.
```

The prompt is editable from the Settings drawer. Manual anchors use account-scoped Codex homes/workspaces, read-only sandboxing, and deny-all approvals.

## Scheduled Anchors

The scheduler creates:

- one same-day 5-hour cadence of daily anchor jobs per enabled account when daily anchors are enabled;
- one weekly target anchor job per enabled account.

The configured daily time is the first wake of the day. Later same-day wakes are derived every 5 hours. For example, `05:00` creates `05:00`, `10:00`, `15:00`, and `20:00`; `09:00` creates `09:00`, `14:00`, and `19:00`.

Schedule edits are saved from the Settings drawer and reloaded without restarting the app. Account cards show the enabled daily weekdays, derived daily wake times, weekly target, and next scheduled wake call.

Smart scheduled-anchor validation:

1. refreshes telemetry before the anchor;
2. skips the anchor when the observed 5-hour window is already active and skip is enabled;
3. runs the anchor when telemetry says no active window exists, or telemetry is unavailable;
4. refreshes telemetry after the anchor;
5. records whether the observed reset timestamp moved.

## Compact Monitor

Open:

```text
http://127.0.0.1:8080/monitor
```

Single-account views:

```text
http://127.0.0.1:8080/monitor/account-a
http://127.0.0.1:8080/monitor/account-b
```

The monitor is dark-only and optimized for small always-on screens. It refreshes every 15 seconds and shows:

- account email when known;
- 5-hour and weekly remaining quota percentages;
- progress bars;
- compact status dots;
- observed or expected reset time.

## Event History

Open:

```text
http://127.0.0.1:8080/history
```

History can be filtered by account, severity level, and category prefix. It includes scheduler events, telemetry listener events, smart-anchor decisions, update events, and anchor failures.

Events with structured payloads can be expanded in the table to inspect stored error details.

## Logs

The application writes rotating logs to:

```text
data/logs/ai-quota-monitor.log
```

Native/Proxmox deployments normally place this under:

```text
/var/lib/ai-quota-monitor/logs/ai-quota-monitor.log
```

Override with `AI_QUOTA_MONITOR_LOG_FILE` when needed. These logs include route and background-service exceptions that are also summarized in the event history.
