#!/bin/bash
# Follow this satellite's service logs (Ctrl-C to stop).
#
# Usage:  ./logs-satellite.sh [extra journalctl args]
#   e.g.  ./logs-satellite.sh -n 500
#         ./logs-satellite.sh --since "1 hour ago"
# Handy:  ln -s /opt/hermes-satellite/scripts/logs-satellite.sh ~/logs-satellite

exec sudo journalctl -u hermes-satellite -n 50 -f "$@"
