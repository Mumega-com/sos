#!/usr/bin/env bash
# journal-tail.sh — fetch last N lines of every SOS-relevant systemd unit.
# Deterministic. Zero tokens. Use before restarting any service.
#
# Usage: journal-tail.sh [N=30]

N="${1:-30}"
UNITS=(
    mirror-api
    athena-redis-listener
    fmaap-hub
    pm2-mumega
    sos-content
    sos-engine
    sos-gateway-bridge
    sos-gateway-mcp
    sos-memory
)
USER_UNITS=(
    agent-wake-daemon
    cortex-events
)

for u in "${UNITS[@]}"; do
    echo "=== $u ==="
    sudo journalctl -u "$u" -n "$N" --no-pager 2>/dev/null | tail -"$N"
    echo
done

for u in "${USER_UNITS[@]}"; do
    echo "=== --user $u ==="
    sudo -u mumega XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u "$u" -n "$N" --no-pager 2>/dev/null | tail -"$N"
    echo
done
