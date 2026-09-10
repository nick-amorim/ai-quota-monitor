#!/usr/bin/env bash
set -euo pipefail

APP_NAME="ai-quota-monitor"
ENV_FILE="${AI_QUOTA_MONITOR_ENV_FILE:-/etc/${APP_NAME}.env}"
INSTALL_DIR="/opt/${APP_NAME}"

if [ -r "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

INSTALL_DIR="${AI_QUOTA_MONITOR_INSTALL_DIR:-$INSTALL_DIR}"
exec "$INSTALL_DIR/.venv/bin/${APP_NAME}-update" "$@"
