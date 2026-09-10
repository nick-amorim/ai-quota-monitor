#!/usr/bin/env bash
set -euo pipefail

APP_NAME="ai-quota-monitor"
REPO_URL="${AI_QUOTA_MONITOR_REPO_URL:-https://github.com/nick-amorim/ai-quota-monitor.git}"
BRANCH="${AI_QUOTA_MONITOR_BRANCH:-main}"
CT_ID="${AI_QUOTA_MONITOR_CT_ID:-}"
HOSTNAME="${AI_QUOTA_MONITOR_HOSTNAME:-ai-quota-monitor}"
DISK_SIZE="${AI_QUOTA_MONITOR_DISK_SIZE:-8}"
MEMORY_MB="${AI_QUOTA_MONITOR_MEMORY_MB:-1024}"
SWAP_MB="${AI_QUOTA_MONITOR_SWAP_MB:-512}"
CPU_CORES="${AI_QUOTA_MONITOR_CPU_CORES:-1}"
STORAGE="${AI_QUOTA_MONITOR_STORAGE:-local-lvm}"
TEMPLATE_STORAGE="${AI_QUOTA_MONITOR_TEMPLATE_STORAGE:-local}"
NET_BRIDGE="${AI_QUOTA_MONITOR_NET_BRIDGE:-vmbr0}"
INSTALL_DIR="/opt/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
ENV_FILE="/etc/${APP_NAME}.env"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
UPDATE_WRAPPER="/usr/local/bin/${APP_NAME}-update"
UPDATE_WRAPPER_PATH_FALLBACK="/usr/bin/${APP_NAME}-update"
LEGACY_UPDATE_WRAPPER="/usr/local/bin/quotapilot-update"
LEGACY_UPDATE_WRAPPER_PATH_FALLBACK="/usr/bin/quotapilot-update"
RESTART_HELPER="/usr/local/sbin/${APP_NAME}-restart"
SUDOERS_FILE="/etc/sudoers.d/${APP_NAME}-restart"
MODE="default"
YES="false"
REPAIR_CHECKOUT="false"

usage() {
  printf '%s\n' \
    "Usage: install-lxc.sh [--advanced] [--yes] [--existing] [--update] [--repair-checkout] [--ct-id ID]" \
    "" \
    "Modes:" \
    "  default       Create a Proxmox LXC when pct is available, then install the app." \
    "  --advanced    Prompt for CT resources before creating the LXC." \
    "  --yes         Non-interactive mode; requires AI_QUOTA_MONITOR_CT_ID or --ct-id on Proxmox hosts." \
    "  --existing    Install into the current Debian/Ubuntu system." \
    "  --update      Update an existing install in the current system." \
    "  --repair-checkout  With --update, save a patch backup and discard tracked local checkout changes."
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --advanced) MODE="advanced" ;;
    --yes|--non-interactive) YES="true" ;;
    --existing) MODE="existing" ;;
    --update) MODE="update" ;;
    --repair-checkout) REPAIR_CHECKOUT="true" ;;
    --ct-id)
      shift
      CT_ID="${1:-}"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage
      exit 1
      ;;
  esac
  shift
done

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    printf 'Run this script as root.\n' >&2
    exit 1
  fi
}

prompt() {
  local label="$1"
  local current="$2"
  local value
  read -r -p "${label} [${current}]: " value
  printf '%s' "${value:-$current}"
}

configure_advanced() {
  [ "$MODE" = "advanced" ] || return 0
  [ "$YES" = "false" ] || return 0
  CT_ID="$(prompt 'Container ID' "$CT_ID")"
  STORAGE="$(prompt 'Storage' "$STORAGE")"
  DISK_SIZE="$(prompt 'Disk GB' "$DISK_SIZE")"
  MEMORY_MB="$(prompt 'Memory MB' "$MEMORY_MB")"
  SWAP_MB="$(prompt 'Swap MB' "$SWAP_MB")"
  CPU_CORES="$(prompt 'CPU cores' "$CPU_CORES")"
  NET_BRIDGE="$(prompt 'Network bridge' "$NET_BRIDGE")"
}

ensure_restart_helper_dependencies() {
  export DEBIAN_FRONTEND=noninteractive
  if ! command -v sudo >/dev/null 2>&1 || ! command -v visudo >/dev/null 2>&1; then
    apt-get update
    apt-get install -y sudo
  fi
  mkdir -p "$(dirname "$RESTART_HELPER")" "$(dirname "$SUDOERS_FILE")"
}

install_inside_current_system() {
  require_root
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y git python3 python3-venv python3-pip curl sudo

  if ! id aiquota >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin aiquota
  fi

  mkdir -p "$DATA_DIR"
  if [ ! -d "$INSTALL_DIR/.git" ]; then
    if [ -e "$INSTALL_DIR" ]; then
      printf '%s exists but is not a Git checkout. Move it aside before installing.\n' "$INSTALL_DIR" >&2
      exit 1
    fi
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    ensure_git_safe_directory
  else
    ensure_git_safe_directory
    git -C "$INSTALL_DIR" fetch origin
    git -C "$INSTALL_DIR" merge --ff-only "origin/${BRANCH}"
  fi

  python3 -m venv "$INSTALL_DIR/.venv"
  "$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
  "$INSTALL_DIR/.venv/bin/python" -m pip install -e "$INSTALL_DIR"

  cat > "$ENV_FILE" <<EOF
AI_QUOTA_MONITOR_ENV=production
AI_QUOTA_MONITOR_HOST=0.0.0.0
AI_QUOTA_MONITOR_PORT=8080
AI_QUOTA_MONITOR_DATABASE_URL=sqlite:///${DATA_DIR}/${APP_NAME}.sqlite3
AI_QUOTA_MONITOR_DATA_DIR=${DATA_DIR}
AI_QUOTA_MONITOR_DEPLOYMENT_MODE=proxmox
AI_QUOTA_MONITOR_INSTALL_DIR=${INSTALL_DIR}
AI_QUOTA_MONITOR_ENABLE_WEB_UPDATES=true
EOF

  cp "$INSTALL_DIR/deploy/systemd/${APP_NAME}.service" "$SERVICE_FILE"
  install_update_wrappers
  install_restart_helper
  repair_install_ownership

  systemctl daemon-reload
  systemctl enable --now "$APP_NAME"
  printf '%s installed. Open http://<container-ip>:8080\n' "$APP_NAME"
}

ensure_git_safe_directory() {
  if git config --system --get-all safe.directory 2>/dev/null | grep -Fx -- "$INSTALL_DIR" >/dev/null; then
    return 0
  fi
  git config --system --add safe.directory "$INSTALL_DIR"
}

install_update_wrappers() {
  cat > "$UPDATE_WRAPPER" <<EOF
#!/usr/bin/env sh
set -eu
env_file="\${AI_QUOTA_MONITOR_ENV_FILE:-${ENV_FILE}}"
if [ -r "\$env_file" ]; then
  set -a
  . "\$env_file"
  set +a
fi
install_dir="\${AI_QUOTA_MONITOR_INSTALL_DIR:-${INSTALL_DIR}}"
exec "\$install_dir/.venv/bin/${APP_NAME}-update" "\$@"
EOF
  chown root:root "$UPDATE_WRAPPER"
  chmod 0755 "$UPDATE_WRAPPER"
  ln -sf "$UPDATE_WRAPPER" "$UPDATE_WRAPPER_PATH_FALLBACK"
  ln -sf "$UPDATE_WRAPPER" "$LEGACY_UPDATE_WRAPPER"
  ln -sf "$UPDATE_WRAPPER" "$LEGACY_UPDATE_WRAPPER_PATH_FALLBACK"

  for path in "$UPDATE_WRAPPER" "$UPDATE_WRAPPER_PATH_FALLBACK" "$LEGACY_UPDATE_WRAPPER" "$LEGACY_UPDATE_WRAPPER_PATH_FALLBACK"; do
    if [ ! -x "$path" ]; then
      printf 'Updater entry point is not executable: %s\n' "$path" >&2
      exit 1
    fi
  done

  if ! PATH="/usr/bin:/bin" command -v "${APP_NAME}-update" >/dev/null 2>&1; then
    printf 'Updater command is not discoverable with a minimal root PATH.\n' >&2
    exit 1
  fi
}

install_restart_helper() {
  ensure_restart_helper_dependencies
  cat > "$RESTART_HELPER" <<EOF
#!/usr/bin/env sh
set -eu
service_name="${APP_NAME}"
systemctl_bin="\$(command -v systemctl)"
if command -v systemd-run >/dev/null 2>&1; then
  unit_name="${APP_NAME}-web-restart-\$(date +%s)"
  exec systemd-run --quiet --on-active=2 --unit "\$unit_name" "\$systemctl_bin" restart "\$service_name"
fi
nohup sh -c "sleep 2; exec '\$systemctl_bin' restart '\$service_name'" >/dev/null 2>&1 &
EOF
  chown root:root "$RESTART_HELPER"
  chmod 0755 "$RESTART_HELPER"
  cat > "$SUDOERS_FILE" <<EOF
aiquota ALL=(root) NOPASSWD: ${RESTART_HELPER}
EOF
  chown root:root "$SUDOERS_FILE"
  chmod 0440 "$SUDOERS_FILE"
  visudo -cf "$SUDOERS_FILE" >/dev/null
}

repair_install_ownership() {
  if ! id aiquota >/dev/null 2>&1; then
    printf 'Service user aiquota does not exist; cannot repair install ownership.\n' >&2
    exit 1
  fi
  mkdir -p "$DATA_DIR"
  chown -R aiquota:aiquota "$INSTALL_DIR" "$DATA_DIR"
  chmod +x "$INSTALL_DIR/scripts/update.sh"
}

bootstrap_checkout_update() {
  local patch_file
  local tracked_status
  ensure_git_safe_directory
  git -C "$INSTALL_DIR" fetch origin
  tracked_status="$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=no)"
  if [ -n "$tracked_status" ]; then
    if [ "$REPAIR_CHECKOUT" = "true" ]; then
      mkdir -p "${DATA_DIR}/backups"
      patch_file="${DATA_DIR}/backups/checkout-repair-$(date -u +%Y%m%d-%H%M%S).patch"
      git -C "$INSTALL_DIR" diff --binary HEAD > "$patch_file"
      git -C "$INSTALL_DIR" restore --source=HEAD --staged --worktree .
      printf 'Tracked checkout changes were saved to %s and restored to HEAD.\n' "$patch_file"
    else
      printf 'Update refused because the checkout has tracked local changes:\n' >&2
      printf '%s\n' "$tracked_status" >&2
      printf 'Review the changes, or rerun with --update --repair-checkout to save a patch backup and restore tracked files.\n' >&2
      exit 1
    fi
  fi
  tracked_status="$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=no)"
  if [ -n "$tracked_status" ]; then
    printf 'Update refused because the checkout has tracked local changes:\n' >&2
    printf '%s\n' "$tracked_status" >&2
    exit 1
  fi
  git -C "$INSTALL_DIR" merge --ff-only "origin/${BRANCH}"
}

update_current_system() {
  require_root
  if [ ! -x "$INSTALL_DIR/.venv/bin/${APP_NAME}-update" ]; then
    printf 'No existing install found at %s\n' "$INSTALL_DIR" >&2
    exit 1
  fi
  ensure_git_safe_directory
  install_update_wrappers
  install_restart_helper
  repair_install_ownership
  bootstrap_checkout_update
  "$UPDATE_WRAPPER" --yes --restart --deployment-mode proxmox
  repair_install_ownership
}

install_into_created_lxc() {
  local raw_url="${REPO_URL%.git}/raw/${BRANCH}/scripts/proxmox/install-lxc.sh"
  local attempt

  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    printf 'Installing %s inside container %s (attempt %s/12)...\n' "$APP_NAME" "$CT_ID" "$attempt"
    if pct exec "$CT_ID" -- bash -lc "
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl
curl -fsSL '$raw_url' | AI_QUOTA_MONITOR_REPO_URL='$REPO_URL' AI_QUOTA_MONITOR_BRANCH='$BRANCH' bash -s -- --existing --yes
"; then
      return 0
    fi

    if [ "$attempt" -lt 12 ]; then
      printf 'Container install attempt failed; waiting for the LXC network/package manager and retrying...\n' >&2
      sleep 10
    fi
  done

  printf 'Failed to install %s inside container %s.\n' "$APP_NAME" "$CT_ID" >&2
  printf 'Enter the container with pct enter %s, then run the --existing installer manually.\n' "$CT_ID" >&2
  exit 1
}

create_lxc() {
  require_root
  configure_advanced
  if [ -z "$CT_ID" ]; then
    if [ "$YES" = "true" ]; then
      printf 'Non-interactive Proxmox mode requires --ct-id or AI_QUOTA_MONITOR_CT_ID.\n' >&2
      exit 1
    fi
    CT_ID="$(prompt 'Container ID' '120')"
  fi

  local template
  template="$(pveam available --section system | awk '/debian-13-standard/ {print $2; exit}')"
  if [ -z "$template" ]; then
    template="$(pveam available --section system | awk '/debian-12-standard/ {print $2; exit}')"
  fi
  if [ -z "$template" ]; then
    printf 'No Debian standard template found in pveam output.\n' >&2
    exit 1
  fi

  pveam download "$TEMPLATE_STORAGE" "$template"
  pct create "$CT_ID" "${TEMPLATE_STORAGE}:vztmpl/${template}" \
    --hostname "$HOSTNAME" \
    --unprivileged 1 \
    --cores "$CPU_CORES" \
    --memory "$MEMORY_MB" \
    --swap "$SWAP_MB" \
    --rootfs "${STORAGE}:${DISK_SIZE}" \
    --net0 "name=eth0,bridge=${NET_BRIDGE},ip=dhcp" \
    --features nesting=1 \
    --onboot 1
  pct start "$CT_ID"
  install_into_created_lxc
  printf 'Container %s created for %s.\n' "$CT_ID" "$APP_NAME"
}

if [ "$MODE" = "update" ]; then
  update_current_system
elif [ "$MODE" = "existing" ] || ! command -v pct >/dev/null 2>&1; then
  install_inside_current_system
else
  create_lxc
fi
