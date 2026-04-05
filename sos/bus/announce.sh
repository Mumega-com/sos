#!/usr/bin/env bash
# Universal agent announce — registers on Redis bus
# Usage: bus-announce.sh <agent-name> <tool> [summary]
# Example: bus-announce.sh gemini-1 gemini "Gemini CLI session on pts/7"

set -euo pipefail

AGENT="${1:?Usage: bus-announce.sh <agent-name> <tool> [summary]}"
TOOL="${2:?Usage: bus-announce.sh <agent-name> <tool> [summary]}"
SUMMARY="${3:-${TOOL} session}"

source /home/mumega/.env.secrets 2>/dev/null || true
REDIS_PASS="${REDIS_PASSWORD:-}"
REDIS="redis-cli -a ${REDIS_PASS} --no-auth-warning"

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
PID=$$
TTY_VAL=$(tty 2>/dev/null || echo "none")
CWD=$(pwd)
MSG_ID=$(python3 -c "import uuid;print(uuid.uuid4())")

# Register in agent registry hash
$REDIS HSET "sos:registry:${AGENT}" \
  name "${AGENT}" \
  tool "${TOOL}" \
  pid "${PID}" \
  tty "${TTY_VAL}" \
  cwd "${CWD}" \
  summary "${SUMMARY}" \
  registered_at "${TIMESTAMP}" \
  last_seen "${TIMESTAMP}" > /dev/null

# Auto-expire if agent doesn't heartbeat in 10 minutes
$REDIS EXPIRE "sos:registry:${AGENT}" 600 > /dev/null

# Announce on global stream
$REDIS XADD "sos:stream:sos:channel:global" '*' \
  id "${MSG_ID}" \
  type announce \
  source "agent:${AGENT}" \
  target "sos:channel:global" \
  payload "{\"text\":\"${AGENT} (${TOOL}) online: ${SUMMARY}\"}" \
  timestamp "${TIMESTAMP}" \
  version "1.0" > /dev/null

echo "Announced: ${AGENT} (${TOOL}) on Redis bus"
