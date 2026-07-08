#!/bin/bash
# Configure this satellite: run the setup wizard against the live config.
#
# Handles the ceremony for a deployed (systemd) satellite:
#   1. stops the daemon if it's running (wizard and daemon share the mic)
#   2. runs the token-protected web wizard against /etc/hermes-satellite
#   3. restarts the daemon afterwards, only if it was running before
#
# Usage:  ./configure-satellite.sh
# Handy:  ln -s /opt/hermes-satellite/scripts/configure-satellite.sh ~/configure-satellite

set -euo pipefail

INSTALL=/opt/hermes-satellite
CONFIG=/etc/hermes-satellite/config.yaml
SERVICE=hermes-satellite

if [ ! -x "$INSTALL/.venv/bin/hermes-satellite" ]; then
    echo "no service install at $INSTALL — for an interactive clone just run:"
    echo "  hermes-satellite setup"
    exit 1
fi

WAS_ACTIVE=0
if systemctl is-active --quiet "$SERVICE"; then
    WAS_ACTIVE=1
    echo "stopping $SERVICE (the wizard needs the microphone)..."
    sudo systemctl stop "$SERVICE"
fi

# Run as root: the live config and secrets.env are root-owned by design.
sudo "$INSTALL/.venv/bin/hermes-satellite" setup --config "$CONFIG" || true

if [ "$WAS_ACTIVE" -eq 1 ]; then
    echo "restarting $SERVICE..."
    sudo systemctl start "$SERVICE"
    sleep 2
    systemctl status "$SERVICE" --no-pager -n 5
else
    echo "$SERVICE was not running before; leaving it stopped."
    echo "start it with:  sudo systemctl start $SERVICE"
fi
