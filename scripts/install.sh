#!/usr/bin/env bash
# MissionDebug one-line installer.
#
#   Robot (agent only, the default):
#     curl -fsSL https://raw.githubusercontent.com/mukul-07/missiondebug/main/scripts/install.sh | sudo bash
#
#   Robot that should report to a hub:
#     curl -fsSL .../install.sh | sudo bash -s -- --hub-url http://192.168.1.50:8000
#
#   Hub machine (backend + web, no agent):
#     curl -fsSL .../install.sh | sudo bash -s -- --hub
#
#   Everything on one machine (single-robot install):
#     curl -fsSL .../install.sh | sudo bash -s -- --all
#
# What it does: adds the signed MissionDebug apt repository, installs the
# selected packages, and (with --hub-url) points the agent's config at your
# hub. Idempotent — safe to re-run. Supports Ubuntu 22.04 (jammy / ROS 2
# Humble) and 24.04 (noble / ROS 2 Jazzy), amd64 + arm64.

set -euo pipefail

REPO_URL="https://mukul-07.github.io/missiondebug"
KEYRING="/etc/apt/keyrings/missiondebug.gpg"
SOURCES="/etc/apt/sources.list.d/missiondebug.list"
CONF="/etc/missiondebug/config.yaml"

MODE="agent"          # agent | hub | all
HUB_URL=""
DRY_RUN=0

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --agent) MODE="agent" ;;
    --hub) MODE="hub" ;;
    --all) MODE="all" ;;
    --hub-url) shift; HUB_URL="${1:-}"; [ -n "$HUB_URL" ] || die "--hub-url needs a value" ;;
    --hub-url=*) HUB_URL="${1#*=}" ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf 'dry-run: %s\n' "$*"
  else
    "$@"
  fi
}

# ---- preflight -----------------------------------------------------------

[ "$(id -u)" = 0 ] || die "run with sudo: curl -fsSL .../install.sh | sudo bash"

command -v apt-get >/dev/null 2>&1 || die "this installer is for Debian/Ubuntu (apt not found)"

CODENAME="$(. /etc/os-release 2>/dev/null && echo "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}")"
case "$CODENAME" in
  jammy|noble) ;;
  "") die "cannot detect the Ubuntu codename (missing /etc/os-release)" ;;
  *) die "unsupported release '$CODENAME' — supported: jammy (22.04), noble (24.04)" ;;
esac

ARCH="$(dpkg --print-architecture)"
case "$ARCH" in
  amd64|arm64) ;;
  *) die "unsupported architecture '$ARCH' — supported: amd64, arm64" ;;
esac

case "$MODE" in
  agent) PKGS="missiondebug-agent" ;;
  hub)   PKGS="missiondebug-backend missiondebug-web" ;;
  all)   PKGS="missiondebug-agent missiondebug-backend missiondebug-web" ;;
esac

log "MissionDebug installer — $CODENAME/$ARCH, installing: $PKGS"

# ---- apt repository (idempotent) -----------------------------------------

if [ ! -f "$KEYRING" ]; then
  log "adding the MissionDebug apt key"
  run install -d -m 0755 /etc/apt/keyrings
  if [ "$DRY_RUN" = 1 ]; then
    printf 'dry-run: curl %s/missiondebug-archive-key.asc | gpg --dearmor -o %s\n' "$REPO_URL" "$KEYRING"
  else
    curl -fsSL "$REPO_URL/missiondebug-archive-key.asc" | gpg --dearmor -o "$KEYRING"
  fi
else
  log "apt key already present"
fi

LINE="deb [signed-by=$KEYRING] $REPO_URL $CODENAME main"
if [ ! -f "$SOURCES" ] || ! grep -qF "$LINE" "$SOURCES" 2>/dev/null; then
  log "adding the apt source ($CODENAME)"
  if [ "$DRY_RUN" = 1 ]; then
    printf 'dry-run: echo "%s" > %s\n' "$LINE" "$SOURCES"
  else
    echo "$LINE" > "$SOURCES"
  fi
else
  log "apt source already present"
fi

log "installing packages"
run apt-get update -qq
# shellcheck disable=SC2086  # PKGS is intentionally word-split
run apt-get install -y $PKGS

# ---- optional hub wiring ---------------------------------------------------

# Single-machine installs (--all) get the local backend as their hub by
# default, so the dashboard's Agents page + live topic discovery work out
# of the box. An explicit --hub-url always wins.
if [ "$MODE" = all ] && [ -z "$HUB_URL" ]; then
  HUB_URL="http://127.0.0.1:8000"
  log "single-machine install: wiring the agent to the local hub ($HUB_URL)"
fi

if [ -n "$HUB_URL" ]; then
  case "$MODE" in
    hub) warn "--hub-url is for robots running the agent; ignoring on a hub-only install" ;;
    *)
      if [ "$DRY_RUN" = 1 ]; then
        printf 'dry-run: append hub config (%s) to %s and restart the agent\n' "$HUB_URL" "$CONF"
      elif [ ! -f "$CONF" ]; then
        warn "$CONF not found — set hub.url manually after the agent's first start"
      elif grep -qE '^hub:' "$CONF"; then
        warn "$CONF already has a hub: section — not touching it"
      else
        log "pointing the agent at $HUB_URL"
        ROBOT_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
        # Reachability from the hub back to this robot needs a non-loopback
        # bind + an explicit callback URL.
        sed -i 's/^http_host:.*/http_host: "0.0.0.0"/' "$CONF"
        {
          echo ""
          echo "# Fleet hub (added by install.sh)"
          echo "hub:"
          echo "  url: \"$HUB_URL\""
          if [ -n "$ROBOT_IP" ]; then
            echo "  agent_url: \"http://$ROBOT_IP:7000\""
          else
            echo "  # agent_url: \"http://<this-robot-ip>:7000\"  # set me: hub->robot callback"
          fi
        } >> "$CONF"
        systemctl restart missiondebug-agent 2>/dev/null || true
      fi
      ;;
  esac
fi

# ---- done ------------------------------------------------------------------

log "done."
echo
case "$MODE" in
  hub)
    echo "Hub dashboard:   http://$(hostname -I 2>/dev/null | awk '{print $1}'):8000"
    echo "Robots report in with:  curl -fsSL .../install.sh | sudo bash -s -- --hub-url http://<this-machine>:8000"
    ;;
  *)
    echo "Next steps:"
    echo "  1. Tailor what gets captured:   sudo missiondebug-agent init   (interactive)"
    echo "     or edit                      sudo nano $CONF"
    echo "  2. Restart after changes:       sudo systemctl restart missiondebug-agent"
    echo "  3. Check it's capturing:        journalctl -u missiondebug-agent -n 20 --no-pager"
    if [ "$MODE" = all ]; then
      echo "  4. Browse your sessions:        http://$(hostname -I 2>/dev/null | awk '{print $1}'):8000"
    fi
    ;;
esac
