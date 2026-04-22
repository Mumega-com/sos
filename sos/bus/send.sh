#!/usr/bin/env bash
# Universal agent-to-agent messaging via Redis
# Usage: bus-send.sh <from> <to> <message>
# Example: bus-send.sh gemini-1 kasra "Build complete, ready for review"

set -euo pipefail

FROM="${1:?Usage: bus-send.sh <from> <to> <message>}"
TO="${2:?Usage: bus-send.sh <from> <to> <message>}"
MESSAGE="${3:?Usage: bus-send.sh <from> <to> <message>}"

source /home/mumega/.env.secrets 2>/dev/null || true
REDIS_PASS="${REDIS_PASSWORD:-}"
REDIS="redis-cli -a ${REDIS_PASS} --no-auth-warning"

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MSG_ID=$(uuidgen 2>/dev/null || python3 -c "import uuid;print(uuid.uuid4())")
STREAM="sos:stream:global:agent:${TO}"
CHANNEL="sos:channel:agent:${TO}"

# Write to stream (persistent)
$REDIS XADD "${STREAM}" '*' \
  id "${MSG_ID}" \
  type "chat" \
  source "agent:${FROM}" \
  target "agent:${TO}" \
  payload "{\"text\":\"${MESSAGE}\"}" \
  timestamp "${TIMESTAMP}" \
  version "1.0" > /dev/null

# Publish to channel (real-time wake)
$REDIS PUBLISH "${CHANNEL}" "{\"id\":\"${MSG_ID}\",\"type\":\"chat\",\"source\":\"agent:${FROM}\",\"target\":\"agent:${TO}\",\"payload\":{\"text\":\"${MESSAGE}\"},\"timestamp\":\"${TIMESTAMP}\"}" > /dev/null

# Poke wake channel
$REDIS PUBLISH "sos:wake:${TO}" "{\"from\":\"${FROM}\",\"text\":\"${MESSAGE}\"}" > /dev/null

echo "Sent: ${FROM} → ${TO}: ${MESSAGE}"
