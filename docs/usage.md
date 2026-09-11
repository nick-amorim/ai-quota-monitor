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

The app starts with two seeded account profiles for compatibility with existing installations:

| Account | Default daily anchor |
| --- | ---: |
| Account A | 05:00 |
| Account B | 09:00 |

Additional accounts can be created from **Settings -> Add account**. Each account has independent Codex runtime state under its own `codex-home` and `workspace` directories. Account authentication is checked and stored as metadata only. The app does not store account credentials or OAuth tokens in SQLite.

Accounts can be archived from the account's `Settings` drawer. Archiving hides the account and stops scheduled anchors, telemetry polling, and live listeners for it, but preserves database history and runtime files.

## Authentication Flow

1. Open `/`.
2. Use `Login` for one account.
3. Complete the Codex ChatGPT device-code flow.
4. Wait for the account card to update automatically, or use `Refresh auth` to force an account metadata check.
5. Repeat for each account.

If two active account profiles resolve to the same Codex account identity, the later refreshed account is marked as `duplicate_account`.

## Quota Telemetry

Connected accounts are refreshed automatically every `AI_QUOTA_MONITOR_USAGE_POLL_INTERVAL_MINUTES` minutes. This background usage polling is configurable from the global settings drawer and refreshes connected, enabled accounts only.

The dashboard and compact monitor also refresh their visible HTML panels on a separate browser-only timer. That interval is controlled by `AI_QUOTA_MONITOR_DASHBOARD_REFRESH_INTERVAL_SECONDS` or the global settings drawer. It does not fetch quota telemetry by itself; it only repaints the UI from the latest stored state.

Use `Refresh now` or `Refresh usage now` when you want an immediate manual quota read instead of waiting for the next automatic usage polling run.

The dashboard and compact monitor display remaining quota percentage, matching the Codex usage menu. The app stores Codex's raw `usedPercent` values and derives remaining quota as `100 - usedPercent` for display.

The app stores:

- raw app-server payloads in `usage_raw`;
- normalized 5-hour and weekly values in `usage_snapshots`;
- observed reset timestamps from Codex;
- expected 5-hour reset timestamps derived from the local daily schedule.

Sparse telemetry responses merge with the previous known snapshot so missing windows do not erase known values.

## Anchor Turns

Manual anchors run immediately from the dashboard for connected accounts.

The default anchor prompt is:

```text
Reply only with OK.
```

The prompt is editable from the global settings drawer. Manual anchors use account-scoped Codex homes/workspaces, read-only sandboxing, and deny-all approvals.

## Scheduled Anchors

The scheduler creates one same-day 5-hour cadence of daily anchor jobs per enabled account when daily anchors are enabled.

The configured daily time is the first wake of the day. Later same-day wakes are derived every 5 hours. For example, `05:00` creates `05:00`, `10:00`, `15:00`, and `20:00`; `09:00` creates `09:00`, `14:00`, and `19:00`.

Schedule edits are saved from each account card's `Settings` button and reloaded without restarting the app. Account cards show the enabled daily weekdays, derived daily wake times, and the next scheduled wake call.

Use `Pause` on an account card to stop only that account's scheduled anchors. Pause does not sign out the account, stop usage polling, hide telemetry, or block a manual anchor. `Resume` restores the configured daily cadence; it does not run an anchor immediately.

There is no configurable weekly anchor. Codex's weekly reset is observed from telemetry, not controlled by a dashboard schedule. To intentionally establish a new weekly cadence, pause scheduled anchors and avoid using that account until the chosen day, then resume it before the next daily anchor. The first subsequent Codex activity determines the observed weekly reset.

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
http://127.0.0.1:8080/monitor/account-3
```

The monitor is dark-only and optimized for small always-on screens. It uses the dashboard refresh interval and shows:

- account email when known;
- 5-hour and weekly remaining quota percentages;
- progress bars;
- compact status dots;
- observed weekly reset time and observed or expected 5-hour reset time.

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
