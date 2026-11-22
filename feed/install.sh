#!/usr/bin/env bash
# Service setup for the addon feed builder on Debian-like Linux.
#
# Run on the feed host from a checkout of this repo, as root:
#   sudo ./feed/install.sh
#
# Idempotent: safe to re-run. Installs packages, copies the worker files,
# creates the config from feed/config.example.json when missing, and enables
# the daily timer (once a day at a random time). Prints next steps (cookie
# login, first check) at the end.
#
# Optional env overrides:
#   INSTALL_DIR  default /opt/addon-sync
#   STATE_DIR    default /var/lib/addon-sync
#   CONFIG_FILE  default /etc/addon-sync.json
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/addon-sync}"
STATE_DIR="${STATE_DIR:-/var/lib/addon-sync}"
CONFIG_FILE="${CONFIG_FILE:-/etc/addon-sync.json}"

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root (sudo ./feed/install.sh)" >&2
  exit 1
fi

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 chromium xvfb x11vnc || {
  echo "WARNING: a package failed (chromium lives in non-free on some setups)" >&2
}
apt-get install -y unrar || echo "WARNING: unrar not installed; rar extraction will fail until it is." >&2

echo "== directories"
install -d -o root -g root -m 0755 "$INSTALL_DIR" "$STATE_DIR" \
  "$STATE_DIR/stage" "$(dirname "$CONFIG_FILE")"

echo "== worker files"
install -o root -g root -m 0755 \
  "$REPO/feed/worker.py" "$REPO/feed/reader.py" \
  "$INSTALL_DIR/"
install -o root -g root -m 0644 "$REPO/feed/addonsync_common.py" "$INSTALL_DIR/"
python3 -m py_compile "$INSTALL_DIR/worker.py" "$INSTALL_DIR/reader.py"   "$INSTALL_DIR/addonsync_common.py"
echo "worker-ok"

echo "== feed presets"
install -d -o root -g root -m 0755 "$INSTALL_DIR/feeds"
if compgen -G "$REPO/feeds/*.json" > /dev/null; then
  install -o root -g root -m 0644 "$REPO"/feeds/*.json "$INSTALL_DIR/feeds/"
  echo "installed $(ls -1 "$INSTALL_DIR/feeds" | wc -l) preset(s)"
fi

echo "== config"
if [ ! -f "$CONFIG_FILE" ]; then
  install -o root -g root -m 0644 "$REPO/feed/config.example.json" "$CONFIG_FILE"
  echo "wrote $CONFIG_FILE from feed/config.example.json - pick a preset, set downloader"
else
  echo "kept existing $CONFIG_FILE"
fi

echo "== timer"
install -o root -g root -m 0644 \
  "$REPO/feed/addonsync-feed.service" "$REPO/feed/addonsync-feed.timer" \
  /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now addonsync-feed.timer
systemctl is-active addonsync-feed.timer
systemctl list-timers addonsync-feed.timer --no-pager

# Every command below, and every one printed for the user, must read the same
# config and state as the service unit does.
export ADDONSYNC_STATE_DIR="$STATE_DIR" ADDONSYNC_CONFIG="$CONFIG_FILE"
ENV="ADDONSYNC_STATE_DIR=$STATE_DIR ADDONSYNC_CONFIG=$CONFIG_FILE"

echo "== browser warm-up (first launch downloads nothing, just starts Chromium)"
# A fresh config has no page to open yet; that is a next step, not a failure.
python3 "$INSTALL_DIR/reader.py" browser | tail -5 ||
  echo "browser not started yet: set preset/thread_url in $CONFIG_FILE first"
python3 "$INSTALL_DIR/reader.py" status | tail -12 || true

echo
echo "== next steps"
echo "1. Give the feed browser your page session (see README 'Signing in to the page'):"
echo "     scp cookies.json root@this-host:$STATE_DIR/cookies.json"
echo "     $ENV python3 $INSTALL_DIR/reader.py import-cookies"
echo "2. Run a check:"
echo "     $ENV python3 $INSTALL_DIR/worker.py check --force"
echo "3. Share $STATE_DIR/publish with your PCs (file share or static HTTP),"
echo "   then run client/Setup.ps1 on each PC pointing at it."
