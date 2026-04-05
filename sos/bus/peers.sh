#!/usr/bin/env bash
# Universal peer discovery — list all agents on Redis bus
# Usage: bus-peers.sh
# Shows both registered agents (registry) and agents with streams

set -euo pipefail

source /home/mumega/.env.secrets 2>/dev/null || true
REDIS_PASS="${REDIS_PASSWORD:-}"
REDIS="redis-cli -a ${REDIS_PASS} --no-auth-warning"

echo "=== Registered Agents (heartbeat active) ==="
KEYS=$($REDIS KEYS "sos:registry:*" 2>/dev/null | sort)
if [ -z "${KEYS}" ]; then
  echo "  (none registered)"
else
  for KEY in ${KEYS}; do
    AGENT="${KEY#sos:registry:}"
    INFO=$($REDIS HGETALL "${KEY}" 2>/dev/null)
    TOOL=$(echo "${INFO}" | awk '/^tool$/{getline; print}')
    SUMMARY=$(echo "${INFO}" | awk '/^summary$/{getline; print}')
    LAST=$(echo "${INFO}" | awk '/^last_seen$/{getline; print}')
    TTY_VAL=$(echo "${INFO}" | awk '/^tty$/{getline; print}')
    printf "  %-15s %-10s %-8s %s  (%s)\n" "${AGENT}" "${TOOL:-?}" "${TTY_VAL:-?}" "${SUMMARY:-}" "${LAST:-?}"
  done
fi

echo ""
echo "=== Agents with Streams (historical) ==="
STREAMS=$($REDIS KEYS "sos:stream:sos:channel:private:agent:*" 2>/dev/null | sort)
if [ -z "${STREAMS}" ]; then
  echo "  (no streams)"
else
  PREFIX="sos:stream:sos:channel:private:agent:"
  for S in ${STREAMS}; do
    AGENT="${S#${PREFIX}}"
    LEN=$($REDIS XLEN "${S}" 2>/dev/null)
    LAST_MSG=$($REDIS XREVRANGE "${S}" + - COUNT 1 2>/dev/null | head -1)
    printf "  %-15s %s messages\n" "${AGENT}" "${LEN:-0}"
  done
fi
