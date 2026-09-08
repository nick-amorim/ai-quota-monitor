#!/usr/bin/env bash
set -euo pipefail

if command -v ai-quota-monitor-update >/dev/null 2>&1; then
  exec ai-quota-monitor-update "$@"
fi

exec /opt/ai-quota-monitor/.venv/bin/ai-quota-monitor-update "$@"
