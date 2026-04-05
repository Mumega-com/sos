#!/usr/bin/env bash
# Universal agent inbox poll — check messages from Redis
# Usage: bus-poll.sh <agent-name> [limit]
# Example: bus-poll.sh gemini-1 5

set -euo pipefail

AGENT="${1:?Usage: bus-poll.sh <agent-name> [limit]}"
LIMIT="${2:-10}"

source /home/mumega/.env.secrets 2>/dev/null || true
REDIS_PASS="${REDIS_PASSWORD:-}"
REDIS="redis-cli -a ${REDIS_PASS} --no-auth-warning"

STREAM="sos:stream:sos:channel:private:agent:${AGENT}"

# Read latest messages
MESSAGES=$($REDIS XREVRANGE "${STREAM}" + - COUNT "${LIMIT}" 2>/dev/null)

if [ -z "${MESSAGES}" ]; then
  echo "No messages for ${AGENT}."
  exit 0
fi

echo "=== Inbox for ${AGENT} (last ${LIMIT}) ==="
echo "${MESSAGES}" | python3 -c "
import sys
lines = sys.stdin.read().strip().split('\n')
i = 0
while i < len(lines):
    line = lines[i].strip()
    if line.startswith('1)') or line.startswith('2)'):
        # Stream entry ID
        pass
    elif 'source' in line.lower() or 'timestamp' in line.lower() or 'payload' in line.lower() or 'type' in line.lower():
        pass
    i += 1
# Fallback: just print raw
for l in lines:
    print(l)
" 2>/dev/null || echo "${MESSAGES}"

# Also refresh heartbeat
$REDIS HSET "sos:registry:${AGENT}" last_seen "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > /dev/null 2>&1 || true
