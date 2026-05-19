#!/usr/bin/env bash
# Universal agent-to-agent messaging via Redis
# Usage: bus-send.sh <from> <to> <message>
# Example: bus-send.sh gemini-1 kasra "Build complete, ready for review"

set -euo pipefail

FROM="${1:?Usage: bus-send.sh <from> <to> <message>}"
TO="${2:?Usage: bus-send.sh <from> <to> <message>}"
MESSAGE="${3:?Usage: bus-send.sh <from> <to> <message>}"

set +u
source /home/sos/.env.secrets 2>/dev/null || true
set -u
REDIS_PASS="${REDIS_PASSWORD:-}"
REDIS="redis-cli -a ${REDIS_PASS} --no-auth-warning"

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MSG_ID=$(uuidgen 2>/dev/null || python3 -c "import uuid;print(uuid.uuid4())")
STREAM="sos:stream:global:agent:${TO}"
CHANNEL="sos:channel:agent:${TO}"

PAYLOAD_JSON=$(python3 - "$MESSAGE" <<'PY'
import json, sys
print(json.dumps({"text": sys.argv[1]}, ensure_ascii=False))
PY
)

PUBSUB_JSON=$(python3 - "$FROM" "$TO" "$MESSAGE" "$TIMESTAMP" "$MSG_ID" <<'PY'
import json, sys
from_agent, to_agent, message, timestamp, message_id = sys.argv[1:6]
print(json.dumps({
    "id": message_id,
    "type": "chat",
    "source": f"agent:{from_agent}",
    "target": f"agent:{to_agent}",
    "payload": {"text": message},
    "timestamp": timestamp,
}, ensure_ascii=False))
PY
)

WAKE_JSON=$(python3 - "$FROM" "$MESSAGE" <<'PY'
import json, sys
from_agent, message = sys.argv[1:3]
print(json.dumps({
    "source": f"agent:{from_agent}",
    "from": from_agent,
    "text": message,
}, ensure_ascii=False))
PY
)

# Write to stream (persistent)
$REDIS XADD "${STREAM}" '*' \
  id "${MSG_ID}" \
  type "chat" \
  source "agent:${FROM}" \
  target "agent:${TO}" \
  payload "${PAYLOAD_JSON}" \
  timestamp "${TIMESTAMP}" \
  version "1.0" > /dev/null

# Publish to channel (real-time wake)
$REDIS PUBLISH "${CHANNEL}" "${PUBSUB_JSON}" > /dev/null

# Poke wake channel
$REDIS PUBLISH "sos:wake:${TO}" "${WAKE_JSON}" > /dev/null

echo "Sent: ${FROM} → ${TO}: ${MESSAGE}"
