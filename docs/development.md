# Development Guide

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Run the app:

```powershell
.\.venv\Scripts\python.exe -m ai_quota_monitor
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Run migrations:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

## Git Flow

Development is organized by phase branches.

```text
git switch main
git pull --ff-only
git switch -c phase/NN-short-name
# implement the phase
# run checks
git commit
git push -u origin phase/NN-short-name
gh pr create --base main --head phase/NN-short-name
```

Each PR should include:

- implementation summary;
- verification results;
- README or docs updates when behavior changes;
- known limitations.

Commit messages should include a short summary and a concise body describing the change.

## Documentation Rules

- `README.md` is the compact user/operator entry point.
- `docs/` is tracked and contains detailed application, deployment, usage, and development docs.
- `develop_docs/` is ignored and reserved for local planning drafts, agent notes, and scratch material.
- Runtime data, account homes, and local databases must never be committed.

## Recommended Verification

For most changes:

```powershell
node --check src\ai_quota_monitor\static\app.js
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m compileall src tests
git diff --check
```

For deployment changes:

```bash
docker compose config
docker compose build
docker compose up -d
```

Then check:

```text
http://127.0.0.1:8080/health
```

## Test Strategy

Automated tests use fake Codex/auth/telemetry/system backends. They should not require real ChatGPT credentials or live Codex network calls.

Keep tests focused on:

- app startup and health;
- database migrations and persistence;
- authentication state transitions;
- telemetry normalization;
- scheduler behavior;
- smart-anchor decisions;
- server-rendered dashboard/monitor output;
- update service safety checks.

## Frontend Strategy

The frontend is intentionally simple:

- Jinja templates;
- one static CSS file;
- one static JavaScript file;
- no Node build step;
- server-rendered partial refreshes.

For monitor changes, prioritize the 3.7-inch Raspberry Pi display:

- dark theme only;
- minimal text;
- account email when available;
- large enough quota percentages;
- compact bars and status dots;
- no timeline or dashboard chrome.
