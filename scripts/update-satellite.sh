#!/bin/bash
# Update this satellite to the latest revision.
#
# Handles the full ceremony for a deployed (systemd) satellite:
#   1. checks whether upstream actually has anything new (no-op exit if not)
#   2. stops the daemon, pulls, reinstalls into the /opt venv (picks up any
#      new dependencies; the board's extra — pi4/pi5 — is auto-detected)
#   3. restarts the daemon only if it was running before, and shows status
#
# Usage:  ./update-satellite.sh [branch]
#   With a branch name, switches this satellite to that branch first (canary
#   pattern: one device rides a feature branch, the rest stay on main).
#   Without one, updates along whatever branch the satellite is already on —
#   a canary stays canaried until you run:  ./update-satellite.sh main
# Handy:  ln -s /opt/hermes-satellite/scripts/update-satellite.sh ~/update-satellite

set -euo pipefail

INSTALL=/opt/hermes-satellite
SERVICE=hermes-satellite
BRANCH="${1:-}"

if [ ! -x "$INSTALL/.venv/bin/hermes-satellite" ]; then
    echo "no service install at $INSTALL — for an interactive clone just run:"
    echo "  git pull && pip install -e ."
    exit 1
fi

sudo git -C "$INSTALL" fetch --quiet
CURRENT=$(sudo git -C "$INSTALL" rev-parse --abbrev-ref HEAD)

if [ -n "$BRANCH" ] && [ "$BRANCH" != "$CURRENT" ]; then
    if ! sudo git -C "$INSTALL" rev-parse --verify --quiet "origin/$BRANCH" > /dev/null; then
        echo "branch '$BRANCH' does not exist on origin — available branches:"
        sudo git -C "$INSTALL" branch -r | grep -v HEAD
        exit 1
    fi
    echo "switching $CURRENT -> $BRANCH"
elif [ -z "$BRANCH" ]; then
    BRANCH="$CURRENT"
fi

LOCAL=$(sudo git -C "$INSTALL" rev-parse HEAD)
REMOTE=$(sudo git -C "$INSTALL" rev-parse "origin/$BRANCH")
if [ "$LOCAL" = "$REMOTE" ]; then
    echo "already at the latest revision of $BRANCH ($(sudo git -C "$INSTALL" log --oneline -1))"
    exit 0
fi

echo "new revisions:"
sudo git -C "$INSTALL" log --oneline "$LOCAL..$REMOTE" || true

WAS_ACTIVE=0
if systemctl is-active --quiet "$SERVICE"; then
    WAS_ACTIVE=1
    echo "stopping $SERVICE..."
    sudo systemctl stop "$SERVICE"
fi

if [ "$BRANCH" != "$CURRENT" ]; then
    sudo git -C "$INSTALL" checkout --quiet "$BRANCH"
fi
sudo git -C "$INSTALL" merge --ff-only --quiet "origin/$BRANCH"

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
echo "now at: [$BRANCH] $(sudo git -C "$INSTALL" log --oneline -1)"
