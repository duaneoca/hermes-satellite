#!/bin/bash
# Update this satellite to the latest revision.
#
# Handles the full ceremony for a deployed (systemd) satellite:
#   1. checks whether upstream actually has anything new (no-op exit if not)
#   2. stops the daemon, pulls, reinstalls into the /opt venv (picks up any
#      new dependencies; the board's extra — pi4/pi5 — is auto-detected)
#   3. restarts the daemon only if it was running before, and shows status
#
# Usage:  ./update-satellite.sh
# Handy:  ln -s /opt/hermes-satellite/scripts/update-satellite.sh ~/update-satellite

set -euo pipefail

INSTALL=/opt/hermes-satellite
SERVICE=hermes-satellite

if [ ! -x "$INSTALL/.venv/bin/hermes-satellite" ]; then
    echo "no service install at $INSTALL — for an interactive clone just run:"
    echo "  git pull && pip install -e ."
    exit 1
fi

sudo git -C "$INSTALL" fetch --quiet
LOCAL=$(sudo git -C "$INSTALL" rev-parse HEAD)
REMOTE=$(sudo git -C "$INSTALL" rev-parse '@{u}')
if [ "$LOCAL" = "$REMOTE" ]; then
    echo "already at the latest revision ($(sudo git -C "$INSTALL" log --oneline -1))"
    exit 0
fi

echo "new revisions:"
sudo git -C "$INSTALL" log --oneline "$LOCAL..$REMOTE"

WAS_ACTIVE=0
if systemctl is-active --quiet "$SERVICE"; then
    WAS_ACTIVE=1
    echo "stopping $SERVICE..."
    sudo systemctl stop "$SERVICE"
fi

sudo git -C "$INSTALL" pull --ff-only --quiet

# Reinstall so new/changed dependencies land; the extra matches the board.
MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)
case "$MODEL" in
    *"Raspberry Pi 5"*) EXTRA="[pi5]" ;;
    *"Raspberry Pi 4"*) EXTRA="[pi4]" ;;
    *)                  EXTRA="" ;;
esac
echo "reinstalling into the venv (extras: ${EXTRA:-none})..."
sudo "$INSTALL/.venv/bin/pip" install --quiet -e "$INSTALL$EXTRA"

if [ "$WAS_ACTIVE" -eq 1 ]; then
    echo "restarting $SERVICE..."
    sudo systemctl start "$SERVICE"
    sleep 2
    systemctl status "$SERVICE" --no-pager -n 5
else
    echo "$SERVICE was not running before; leaving it stopped."
fi
echo "now at: $(sudo git -C "$INSTALL" log --oneline -1)"
